"""行业板块日更任务：K 线增量 → 拥挤度快照 → 月度倾斜留痕 → 估值分位。

盘后 cron 触发（见 main.py），全流程幂等：
- K 线按 (code, ts) 插缺的，并用源数据覆盖最近 REFRESH_BARS 根（实时快照补的当日 bar 等官方日线出来后被校正）；
  首轮自动全量 bootstrap；源返回比库里还旧的数据（申万偶发旧快照）时重试一次；
- 申万源收盘后再用实时快照补当日 bar（历史日线常到次日才发布），按“快照昨收 = 库里最后收盘”对齐交易日；
- 拥挤度只算 sector_metric 里还没有的日期（限最近 METRIC_BACKFILL 根，
  纯 Python 滚动分位全量回填太慢且展示只需一年）；
- 倾斜只算 sector_tilt 里还没有的完整月份；
- 估值（仅申万源）首采逐行业拉 VAL_FULL_YEARS 年，之后一次请求取全行业增量；
  分位只补最近 VALUATION_BACKFILL 日里还没算的行。

口径锁定：库里已有 sector_info 时沿用其 source，避免申万/东财混面板。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.core.config import settings
from app.core.db import SessionLocal
from app.jobs.collect import in_trading_session
from app.models.market import Kline
from app.models.sector import SectorBar, SectorInfo, SectorMetric, SectorTilt, SectorValuation
from kairos_data.sources.sector import get_sector_source, throttle
from app.services.sector_metrics import build_panel, crowding_rows, month_key, tilt_rows
from app.services.sector_valuation import rolling_pct

logger = logging.getLogger("kairos.sector")

FULL_DEPTH = 3200      # 首采深度（约 13 年，season 因子需要 ≥6 年同月样本）
INCR_DEPTH = 40        # 日常增量深度（覆盖停机数日的缺口）
REFRESH_BARS = 5       # 已有的最近几根用源数据覆盖（校正快照补的 bar）
SNAPSHOT_AFTER = (15, 5)  # 实时快照在这之后才算当日终值（CST）
CAL_REF = "600519"     # 交易日历参照股（个股日 K）
METRIC_BACKFILL = 420  # 拥挤度最多回填的交易日数（展示约一年足够）
VAL_FULL_YEARS = 8     # 估值首采年数（5 年分位窗口 + 一年多回填）
VAL_INCR_DAYS = 15     # 估值增量回看自然日数（覆盖停机缺口）
VALUATION_BACKFILL = 420


def _locked_source(db) -> str | None:
    row = db.execute(select(SectorInfo.source).limit(1)).scalar()
    return row


def load_bar_panel(db, tail: int | None = None):
    """从库里读全部板块 K 线构建面板。tail 只取每板块最近 N 根（RRG/预览用）。"""
    bars: dict[str, list[tuple[datetime, float, float]]] = {}
    rows = db.execute(
        select(SectorBar.code, SectorBar.ts, SectorBar.close, SectorBar.amount)
        .order_by(SectorBar.code, SectorBar.ts)
    ).all()
    for code, ts, close, amount in rows:
        bars.setdefault(code, []).append((ts, close, amount or 0.0))
    if tail:
        bars = {c: b[-tail:] for c, b in bars.items()}
    return build_panel(bars) if bars else None


def _sync_bars(db, src) -> int:
    sectors = src.list_sectors()
    for code, name in sectors:
        existing = db.get(SectorInfo, code)
        if existing is None:
            db.add(SectorInfo(code=code, name=name, source=src.name))
        elif existing.name != name:
            existing.name = name
    db.commit()

    last_ts = {
        code: ts
        for code, ts in db.execute(
            select(SectorBar.code, func.max(SectorBar.ts)).group_by(SectorBar.code)
        )
    }
    inserted = 0
    for code, _name in sectors:
        limit = INCR_DEPTH if code in last_ts else FULL_DEPTH
        floor = last_ts.get(code)
        try:
            bars = src.get_bars(code, limit)
            if floor is not None and bars and bars[-1].ts < floor:
                # 申万 trend 接口偶发返回旧快照（比库里还旧），稍等重拉一次
                logger.warning("Sector %s: source returned stale data (latest %s < db %s), retrying",
                               code, bars[-1].ts.date(), floor.date())
                time.sleep(3)
                bars = src.get_bars(code, limit)
        except Exception as exc:  # noqa: BLE001 — 单个板块失败不拦全局
            logger.warning("Sector kline fetch failed for %s: %s", code, exc)
            continue
        recent = {
            b.ts: b for b in db.execute(
                select(SectorBar).where(SectorBar.code == code).order_by(SectorBar.ts.desc()).limit(REFRESH_BARS)
            ).scalars()
        } if floor is not None else {}
        for b in bars:
            if floor is not None and b.ts <= floor:
                row = recent.get(b.ts)
                if row is not None:
                    _assign(row, b)
                continue
            db.add(
                SectorBar(
                    code=code, ts=b.ts, open=b.open, high=b.high, low=b.low,
                    close=b.close, volume=b.volume, amount=b.amount,
                )
            )
            inserted += 1
        throttle()
    db.commit()
    return inserted


def _assign(row: SectorBar, b) -> None:
    for f in ("open", "high", "low", "close", "volume", "amount"):
        v = getattr(b, f)
        if getattr(row, f) != v:
            setattr(row, f, v)


def _sync_snapshot(db, src) -> int:
    """用实时快照补“下一个交易日”的 bar（收盘后才补当日）。返回补了几根。

    快照没有日期字段：只有快照的昨收与库里该板块最后一根收盘一致时，才认定快照属于
    交易日历上紧接着的那一天——快照已滚到更晚的日子或库里缺多天时，昨收对不上，自然跳过。
    """
    get = getattr(src, "get_snapshot", None)
    if get is None:
        return 0
    try:
        snap = get()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Sector snapshot fetch failed: %s", exc)
        return 0
    if not snap:
        return 0
    now = datetime.now(timezone.utc) + timedelta(hours=8)
    today = datetime(now.year, now.month, now.day)
    last = _last_bars(db)
    if not last:
        return 0
    earliest = min(ts for ts, _ in last.values())
    cal = sorted(r[0] for r in db.execute(
        select(Kline.ts).where(Kline.code == CAL_REF, Kline.period == "1d", Kline.ts > earliest)
    ))
    added = 0
    for code, sn in snap.items():
        if code not in last or min(sn.open, sn.high, sn.low, sn.close) <= 0:
            continue
        ts, close = last[code]
        if abs(sn.prev_close - close) > 0.011:
            continue
        nxt = next((d for d in cal if d > ts), None)
        if nxt is None or nxt > today:
            continue
        if nxt == today and (now.hour, now.minute) < SNAPSHOT_AFTER:
            continue  # 午休 / 盘前时快照还不是当日终值
        db.add(SectorBar(code=code, ts=nxt, open=sn.open, high=sn.high, low=sn.low, close=sn.close,
                         volume=sn.volume, amount=sn.amount))
        added += 1
    db.commit()
    return added


def _last_bars(db) -> dict[str, tuple[datetime, float]]:
    latest = dict(db.execute(select(SectorBar.code, func.max(SectorBar.ts)).group_by(SectorBar.code)).all())
    if not latest:
        return {}
    rows = db.execute(
        select(SectorBar.code, SectorBar.ts, SectorBar.close).where(SectorBar.ts >= min(latest.values()))
    ).all()
    return {code: (ts, close) for code, ts, close in rows if latest.get(code) == ts}


def _sync_metrics(db) -> int:
    panel = load_bar_panel(db)
    if panel is None or not panel.dates:
        return 0
    have: dict[str, set] = {}
    for code, ts in db.execute(select(SectorMetric.code, SectorMetric.ts)):
        have.setdefault(code, set()).add(ts)
    since = panel.dates[max(0, len(panel.dates) - METRIC_BACKFILL)]
    written = 0
    for row in crowding_rows(panel, since=since):
        if row["ts"] in have.get(row["code"], ()):  # 幂等：已有的日期不动
            continue
        db.add(
            SectorMetric(
                code=row["code"], ts=row["ts"], crowd=row["crowd"],
                crowd_share=row["share"], crowd_heat=row["heat"], crowd_bias=row["bias"],
            )
        )
        written += 1
    db.commit()
    return written


def _sync_tilts(db) -> int:
    panel = load_bar_panel(db)
    if panel is None or not panel.dates:
        return 0
    have = {(code, month) for code, month in db.execute(select(SectorTilt.code, SectorTilt.month))}
    done_months = {m for _, m in have}
    # 只算缺的完整月份（tilt_rows 自身会剔掉最后一个不完整月）
    candidate = {month_key(d) for d in panel.dates} - done_months
    if not candidate:
        return 0
    written = 0
    for row in tilt_rows(panel, only_months=candidate):
        if (row["code"], row["month"]) in have:
            continue
        db.add(
            SectorTilt(
                code=row["code"], month=row["month"], rev1_z=row["rev1_z"],
                season_z=row["season_z"], resmom_z=row["resmom_z"],
                score=row["score"], rank=row["rank"], suggestion=row["suggestion"],
            )
        )
        written += 1
    db.commit()
    return written


def _sync_valuations(db, src) -> int:
    """估值原值增量入库 + 补算分位。东财源没有估值接口，直接跳过。"""
    if not hasattr(src, "get_valuations"):
        return 0
    codes = [code for code, _ in src.list_sectors()]
    last_ts = {
        code: ts
        for code, ts in db.execute(
            select(SectorValuation.code, func.max(SectorValuation.ts)).group_by(SectorValuation.code)
        )
    }
    today = datetime.now()
    batches = []
    missing = [c for c in codes if c not in last_ts]
    for code in missing:  # 首采：单行业长区间
        try:
            batches.append(src.get_valuations(code, today - timedelta(days=365 * VAL_FULL_YEARS), today))
        except Exception as exc:  # noqa: BLE001 — 单个板块失败不拦全局
            logger.warning("Sector valuation fetch failed for %s: %s", code, exc)
        throttle()
    if last_ts:  # 增量：一次请求取全部行业
        start = min(last_ts.values()) - timedelta(days=VAL_INCR_DAYS)
        try:
            batches.append(src.get_valuations("all", start, today))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Sector valuation incremental fetch failed: %s", exc)

    inserted = 0
    seen: set[tuple[str, datetime]] = set()
    for rows in batches:
        for v in rows:
            floor = last_ts.get(v.code)
            key = (v.code, v.ts)
            if v.code not in codes or (floor is not None and v.ts <= floor) or key in seen:
                continue
            seen.add(key)
            db.add(SectorValuation(code=v.code, ts=v.ts, pe=v.pe, pb=v.pb, dp=v.dp))
            inserted += 1
    db.commit()

    # 补算分位：每个板块最近 VALUATION_BACKFILL 行里两个分位都还空的
    by_code: dict[str, list[SectorValuation]] = {}
    for row in db.execute(
        select(SectorValuation).order_by(SectorValuation.code, SectorValuation.ts)
    ).scalars():
        by_code.setdefault(row.code, []).append(row)
    for rows in by_code.values():
        pes = [r.pe for r in rows]
        pbs = [r.pb for r in rows]
        for i in range(max(0, len(rows) - VALUATION_BACKFILL), len(rows)):
            r = rows[i]
            if r.pe_pct is None and r.pb_pct is None:
                r.pe_pct = rolling_pct(pes, i)
                r.pb_pct = rolling_pct(pbs, i)
    db.commit()
    return inserted


def update_sectors() -> None:
    """盘后日更入口。盘中跳过（收盘值才有意义）；数据源探测失败跳过本轮。"""
    if settings.sector_source == "off":
        return
    if in_trading_session():
        return
    db = SessionLocal()
    try:
        src = get_sector_source(_locked_source(db) or settings.sector_source)
        if src is None:
            logger.info("No sector source available; skipping update")
            return
        n_bars = _sync_bars(db, src)
        n_bars += _sync_snapshot(db, src)
        n_metrics = _sync_metrics(db)
        n_tilts = _sync_tilts(db)
        n_vals = _sync_valuations(db, src)
        logger.info(
            "Sector update (%s): %d bars, %d metrics, %d tilt rows, %d valuation rows",
            src.name, n_bars, n_metrics, n_tilts, n_vals,
        )
    except Exception as exc:  # noqa: BLE001 — keep the scheduler alive
        logger.warning("Sector update failed: %s", exc)
    finally:
        db.close()
