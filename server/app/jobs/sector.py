"""行业板块日更任务：K 线增量 → 拥挤度快照 → 月度倾斜留痕。

盘后 cron 触发（见 main.py），全流程幂等：
- K 线按 (code, ts) 只插缺的；首轮自动全量 bootstrap；
- 拥挤度只算 sector_metric 里还没有的日期（限最近 METRIC_BACKFILL 根，
  纯 Python 滚动分位全量回填太慢且展示只需一年）；
- 倾斜只算 sector_tilt 里还没有的完整月份。

口径锁定：库里已有 sector_info 时沿用其 source，避免申万/东财混面板。
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import func, select

from app.core.config import settings
from app.core.db import SessionLocal
from app.jobs.collect import in_trading_session
from app.models.sector import SectorBar, SectorInfo, SectorMetric, SectorTilt
from app.providers.sector_provider import get_sector_source, throttle
from app.services.sector_metrics import build_panel, crowding_rows, month_key, tilt_rows

logger = logging.getLogger("kairos.sector")

FULL_DEPTH = 3200      # 首采深度（约 13 年，season 因子需要 ≥6 年同月样本）
INCR_DEPTH = 40        # 日常增量深度（覆盖停机数日的缺口）
METRIC_BACKFILL = 420  # 拥挤度最多回填的交易日数（展示约一年足够）


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
        try:
            bars = src.get_bars(code, limit)
        except Exception as exc:  # noqa: BLE001 — 单个板块失败不拦全局
            logger.warning("Sector kline fetch failed for %s: %s", code, exc)
            continue
        floor = last_ts.get(code)
        for b in bars:
            if floor is not None and b.ts <= floor:
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
        n_metrics = _sync_metrics(db)
        n_tilts = _sync_tilts(db)
        logger.info(
            "Sector update (%s): %d bars, %d metrics, %d tilt rows",
            src.name, n_bars, n_metrics, n_tilts,
        )
    except Exception as exc:  # noqa: BLE001 — keep the scheduler alive
        logger.warning("Sector update failed: %s", exc)
    finally:
        db.close()
