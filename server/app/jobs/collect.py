"""Data collector: seeds reference data once, refreshes quotes/indices each tick.

Runs every `collect_interval_seconds` via APScheduler (see main.py). With a
live provider (akshare/tencent) it only collects during A-share trading
sessions; with the seed provider it always refreshes so the demo keeps moving.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from time import sleep

from sqlalchemy import delete, func, select, update

from app.core.db import SessionLocal
from app.models.market import (
    AdjustmentEvent,
    FactorSnapshot,
    Fundamental,
    IndexQuote,
    Kline,
    Quote,
    StockInfo,
)
from app.providers.factory import get_provider

logger = logging.getLogger("kairos.collector")

# China Standard Time = UTC+8; sessions 09:25–11:30 and 13:00–15:00 —
# 早盘从 9:25 开始：集合竞价撮合已结束，行情源已给出开盘价。
_CST_OFFSET_HOURS = 8

# 日 K 目标深度（约 3 年交易日），供 bootstrap / 回补 / 加深共用。
KLINE_DEPTH = 760


def in_trading_session(now_utc: datetime | None = None) -> bool:
    now = now_utc or datetime.now(timezone.utc)
    cst_hour = (now.hour + _CST_OFFSET_HOURS) % 24
    t = time(cst_hour, now.minute)
    if now.weekday() >= 5:  # Sat/Sun
        return False
    return (time(9, 25) <= t <= time(11, 30)) or (time(13, 0) <= t <= time(15, 0))


# 除权检测当日已完成的标记（CST 日期字符串）。重启后重跑无害：
# 历史已重标定 → 比例重算 ≈ 1，且事件表 (code, date) 去重
_adj_done_day: str | None = None

# 复权比例的合法区间：分红/送转/配股都是下调（10送10 → 0.5），
# 上调（缩股）极罕见不处理；|r-1| ≤ 0.1% 视为正常价差不动
_ADJ_MIN, _ADJ_EPS = 0.2, 0.001


def _apply_adjustments(db, quotes, day_start: datetime) -> int:
    """除权除息当日检测 + 历史重标定，维持全库"连续前复权"口径。

    交易所披露的 prev_close 与库里昨收出现超阈值下调 → 除权事件：
    把该股全部历史 bar 按比例缩放（假跌恰好被抵消，任意两日比价关系
    保持正确），并同步缩放模拟盘持仓的成本价/最新价。读路径零改动。

    已知边界：停机跨过 ex-date 时，比例里会混入当日真实涨跌（误差一次
    性、幅度小），季度 kline-full 重刷兜底修正。
    """
    todays = {
        q.code: q for q in quotes if q.exchange_ts and q.exchange_ts.date() == day_start.date()
    }
    if not todays:
        return 0
    # 每股最新一根 ts < today 的收盘（库里口径的"昨收"）
    rn = func.row_number().over(partition_by=Kline.code, order_by=Kline.ts.desc()).label("rn")
    sub = (
        select(Kline.code, Kline.close, rn)
        .where(Kline.period == "1d", Kline.ts < day_start, Kline.code.in_(sorted(todays)))
        .subquery()
    )
    prev_stored = {
        code: close for code, close in db.execute(
            select(sub.c.code, sub.c.close).where(sub.c.rn == 1)
        )
    }
    seen_today = {
        r[0] for r in db.execute(
            select(AdjustmentEvent.code).where(AdjustmentEvent.date == day_start)
        )
    }
    applied = 0
    for code, q in todays.items():
        stored = prev_stored.get(code)
        if code in seen_today or not stored or stored <= 0 or q.prev_close <= 0:
            continue
        r = q.prev_close / stored
        if not (_ADJ_MIN <= r <= 1.0) or abs(r - 1) <= _ADJ_EPS:
            continue
        db.execute(
            update(Kline)
            .where(Kline.code == code, Kline.period == "1d", Kline.ts < day_start)
            .values(open=Kline.open * r, high=Kline.high * r,
                    low=Kline.low * r, close=Kline.close * r)
        )
        # 模拟盘持仓与排队的价格基准同口径缩放（否则止损阈值/浮盈会按假跌算）
        from app.models.strategy import PaperPosition

        db.execute(
            update(PaperPosition)
            .where(PaperPosition.code == code)
            .values(entry_px=PaperPosition.entry_px * r,
                    last_price=PaperPosition.last_price * r)
        )
        db.add(AdjustmentEvent(code=code, date=day_start, ratio=round(r, 6)))
        applied += 1
        logger.info("Adjustment %s: ratio %.4f (prev %.3f -> %.3f)",
                    code, r, stored, q.prev_close)
    if applied:
        db.commit()
    return applied


def _refresh_quotes(db) -> int:
    provider = get_provider()
    quotes = provider.get_quotes()

    # 除权检测每个交易日只跑一次（首采时），seed 演示数据不适用
    global _adj_done_day
    cst_today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
    if provider.name != "seed" and _adj_done_day != str(cst_today):
        day_start = datetime(cst_today.year, cst_today.month, cst_today.day)
        try:
            _apply_adjustments(db, quotes, day_start)
        except Exception:  # noqa: BLE001 — 检测失败不拦行情写入
            logger.exception("adjustment detection failed")
            db.rollback()
        _adj_done_day = str(cst_today)

    fund_map = {f.code: f for f in db.execute(select(Fundamental)).scalars().all()}
    for q in quotes:
        roe = q.roe or (fund_map[q.code].roe if q.code in fund_map else 0.0)
        db.merge(
            Quote(
                code=q.code,
                ts=q.ts or datetime.now(timezone.utc),
                price=q.price,
                prev_close=q.prev_close,
                open=q.open,
                high=q.high,
                low=q.low,
                volume=q.volume,
                turnover=q.turnover,
                turnover_rate=q.turnover_rate,
                pe=q.pe,
                pb=q.pb,
                market_cap=q.market_cap,
                roe=roe,
            )
        )
    for idx in provider.get_indices():
        db.merge(
            IndexQuote(
                code=idx.code,
                name=idx.name,
                ts=datetime.now(timezone.utc),
                price=idx.price,
                change=idx.change,
                change_pct=idx.change_pct,
            )
        )
    _upsert_today_bars(db, quotes)
    db.commit()
    return len(quotes)


def _upsert_today_bars(db, quotes) -> None:
    """把当日行情合成/覆盖为当日日 K bar，让 K 线保持到最新交易日。

    日 K 只在 bootstrap 时批量拉过一次，之后全靠这里逐分钟刷新当日 bar
    （收盘后最后一次刷新即当日收盘 bar）。只处理交易所时间戳落在今天的
    行情——节假日/停牌时腾讯返回旧数据，不会被误写成今天的 bar。
    """
    cst_today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
    todays = [q for q in quotes if q.exchange_ts and q.exchange_ts.date() == cst_today]
    if not todays:
        return
    day_start = datetime(cst_today.year, cst_today.month, cst_today.day)
    db.execute(delete(Kline).where(Kline.period == "1d", Kline.ts == day_start))
    for q in todays:
        db.add(
            Kline(
                code=q.code, period="1d", ts=day_start,
                open=q.open, high=q.high, low=q.low, close=q.price, volume=q.volume,
            )
        )


def bootstrap(db) -> None:
    """Populate reference data (universe, klines, fundamentals) if empty."""
    provider = get_provider()

    if db.execute(select(func.count()).select_from(StockInfo)).scalar() == 0:
        for meta in provider.get_universe():
            db.merge(
                StockInfo(code=meta.code, name=meta.name, market=meta.market, industry=meta.industry)
            )
        db.commit()
        logger.info("Seeded stock universe")

    if db.execute(select(func.count()).select_from(Fundamental)).scalar() == 0:
        for f in provider.get_fundamentals():
            db.merge(
                Fundamental(
                    code=f.code, pe=f.pe, pb=f.pb, roe=f.roe,
                    market_cap=f.market_cap, dividend_yield=f.dividend_yield,
                )
            )
        db.commit()
        logger.info("Seeded fundamentals")

    if db.execute(select(func.count()).select_from(Kline)).scalar() == 0:
        codes = [m.code for m in provider.get_universe()]
        failed = 0
        for i, code in enumerate(codes, 1):
            sleep(0.05)  # pace the burst — thousands of rapid calls trip provider rate limits
            try:
                candles = provider.get_kline(code, "1d", KLINE_DEPTH)
            except Exception as exc:  # noqa: BLE001 — one bad ticker must not kill startup
                logger.warning("Kline fetch failed for %s: %s", code, exc)
                failed += 1
                continue
            for c in candles:
                db.add(
                    Kline(
                        code=code, period="1d", ts=c.ts,
                        open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume,
                    )
                )
            if i % 200 == 0:
                db.commit()  # bound the transaction; keep partial progress on crash
                logger.info("Seeded K-lines: %d/%d", i, len(codes))
        db.commit()
        logger.info("Seeded K-lines for %d stocks (%d failed)", len(codes) - failed, failed)


def refresh_reference() -> None:
    """Refresh slow-moving reference data: industry classification + fundamentals
    (roe / dividend_yield). Runs in the scheduler right after startup and daily —
    bootstrap only seeds empty tables, so existing DBs rely on this to heal the
    placeholder "—" / 0.0 values."""
    provider = get_provider()
    if provider.name == "seed":
        return
    db = SessionLocal()
    try:
        metas = provider.get_universe(refresh=True)
        n_ind = 0
        for meta in metas:
            info = db.get(StockInfo, meta.code)
            if info is None:
                # 新上市股票即使还没行业分类也要入库，否则前端只能显示裸代码
                db.add(StockInfo(code=meta.code, name=meta.name, market=meta.market, industry=meta.industry))
            elif meta.industry != "—" and info.industry != meta.industry:
                info.industry = meta.industry
            if meta.industry != "—":
                n_ind += 1
        for f in provider.get_fundamentals():
            db.merge(
                Fundamental(
                    code=f.code, pe=f.pe, pb=f.pb, roe=f.roe,
                    market_cap=f.market_cap, dividend_yield=f.dividend_yield,
                )
            )
        db.commit()

        # 给 bootstrap 之后新上市的股票回补日 K（没有 K 线的股票会让回测缺数据）。
        # 每轮最多补 300 只，既覆盖日常新增，又不至于在数据大面积缺失时打爆限流。
        have_kline = {
            r[0] for r in db.execute(select(Kline.code).where(Kline.period == "1d").distinct())
        }
        missing = [m.code for m in metas if m.code not in have_kline][:300]
        backfilled = 0
        for code in missing:
            sleep(0.05)
            try:
                candles = provider.get_kline(code, "1d", KLINE_DEPTH)
            except Exception:  # noqa: BLE001 — one bad ticker must not kill the job
                continue
            for c in candles:
                db.add(
                    Kline(
                        code=code, period="1d", ts=c.ts,
                        open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume,
                    )
                )
            backfilled += 1
        db.commit()

        repaired = _repair_kline_gaps(db, provider)
        deepened = _deepen_kline_history(db, provider)
        logger.info(
            "Reference refresh: industry for %d stocks, fundamentals updated, "
            "klines backfilled for %d new stocks, gaps repaired for %d stocks, "
            "history deepened for %d stocks",
            n_ind, backfilled, repaired, deepened,
        )
    except Exception as exc:  # noqa: BLE001 — keep the scheduler alive
        logger.warning("Reference refresh failed: %s", exc)
    finally:
        db.close()


def _repair_kline_gaps(db, provider, window: int = 40, cap: int = 6000) -> int:
    """按数据源的真实交易日历检测并回补日 K 缺口。

    当日合成 bar 只覆盖"今天"——服务停摆几天、或历史上日 K 从未随日更新
    的欠账，会在中间留洞，均线会静默跨洞算错。这里用一只常年不停牌的
    参照股（贵州茅台）拿到真实交易日序列，逐股找出缺失日期再从数据源补。
    长期停牌股会反复被扫到但补不回来（数据源本来就没有），量小、无害。
    """
    try:
        ref = provider.get_kline("600519", "1d", window + 20)
    except Exception:  # noqa: BLE001
        return 0
    truth = {c.ts for c in ref[-window:]}
    if not truth:
        return 0
    window_start = min(truth)

    have: dict[str, set] = {}
    for code, ts in db.execute(
        select(Kline.code, Kline.ts).where(Kline.period == "1d", Kline.ts >= window_start)
    ):
        have.setdefault(code, set()).add(ts)

    repaired = 0
    for code, dates in have.items():
        if repaired >= cap:
            logger.warning("Kline gap repair capped at %d stocks; continuing next run", cap)
            break
        first = min(dates)  # 窗口中段上市的新股，只要求上市之后的日期
        missing = {d for d in truth if d > first and d not in dates}
        if not missing:
            continue
        sleep(0.05)
        try:
            candles = provider.get_kline(code, "1d", window + 20)
        except Exception:  # noqa: BLE001
            continue
        for c in candles:
            if c.ts in missing:
                db.add(
                    Kline(
                        code=code, period="1d", ts=c.ts,
                        open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume,
                    )
                )
        repaired += 1
        if repaired % 500 == 0:
            db.commit()
            logger.info("Kline gap repair progress: %d", repaired)
    db.commit()
    return repaired


def _deepen_kline_history(db, provider, cap: int = 3000) -> int:
    """把存量股票的日 K 深度扩到 KLINE_DEPTH（约 3 年），支撑长区间回测。

    早期 bootstrap 只拉了 250 根；这里对深度不足的股票重拉更长历史，
    只插入库里没有的日期。上市不满 3 年的新股每天会被扫到但插不进新行，
    属于少量无害的空转调用。
    """
    counts = {
        code: n
        for code, n in db.execute(
            select(Kline.code, func.count()).where(Kline.period == "1d").group_by(Kline.code)
        )
    }
    deepened = 0
    for code, n in counts.items():
        if n >= KLINE_DEPTH - 60:
            continue
        if deepened >= cap:
            logger.warning("Kline deepen capped at %d stocks; continuing next run", cap)
            break
        sleep(0.05)
        try:
            candles = provider.get_kline(code, "1d", KLINE_DEPTH)
        except Exception:  # noqa: BLE001 — one bad ticker must not kill the job
            continue
        if len(candles) <= n:
            continue  # 新股：历史本来就这么长，不算加深
        have = {
            r[0]
            for r in db.execute(
                select(Kline.ts).where(Kline.code == code, Kline.period == "1d")
            )
        }
        for c in candles:
            if c.ts not in have:
                db.add(
                    Kline(
                        code=code, period="1d", ts=c.ts,
                        open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume,
                    )
                )
        deepened += 1
        if deepened % 500 == 0:
            db.commit()
            logger.info("Kline deepen progress: %d", deepened)
    db.commit()
    return deepened


def snapshot_factors() -> None:
    """收盘后归档当个交易日的全市场因子快照（point-in-time 回测的数据源）。

    幂等：以库里最新日 K 的日期为快照日，已存在即跳过；盘中不拍
    （盘中值不是收盘值）。停机漏拍的日子无法补（quotes 只有最新一份），
    但下一个交易日会正常续上。
    """
    db = SessionLocal()
    try:
        if in_trading_session():
            return
        snap_day = db.execute(
            select(func.max(Kline.ts)).where(Kline.period == "1d")
        ).scalar()
        if snap_day is None:
            return
        exists = db.execute(
            select(func.count()).select_from(FactorSnapshot).where(FactorSnapshot.ts == snap_day)
        ).scalar()
        if exists:
            return
        from app.services.market import factor_rows

        rows = factor_rows(db)
        for r in rows:
            db.add(
                FactorSnapshot(
                    code=r["code"], ts=snap_day,
                    pe=r["pe"], pb=r["pb"], roe=r["roe"],
                    turnover_rate=r["turnover_rate"], turnover=r["turnover"],
                    market_cap=r["market_cap"], change_pct=r["change_pct"],
                    price=r["price"], dividend_yield=r["dividend_yield"],
                    industry=r["industry"],
                )
            )
        db.commit()
        logger.info("Factor snapshot for %s: %d stocks", snap_day.date(), len(rows))
    except Exception as exc:  # noqa: BLE001 — keep the scheduler alive
        logger.warning("Factor snapshot failed: %s", exc)
    finally:
        db.close()


def collect_once(force: bool = False) -> None:
    provider = get_provider()
    if not force and provider.name != "seed" and not in_trading_session():
        logger.debug("Outside trading session; skipping collection")
        return
    db = SessionLocal()
    try:
        n = _refresh_quotes(db)
        logger.info("Collected %d quotes", n)
    except Exception as exc:  # noqa: BLE001 — keep the scheduler alive
        logger.warning("Collection failed: %s", exc)
    finally:
        db.close()


def run_bootstrap_and_first_collect() -> None:
    db = SessionLocal()
    try:
        bootstrap(db)
    finally:
        db.close()
    # Force the first collection even off-hours so a fresh deploy serves the
    # latest close instead of empty dashboards until the next trading session.
    collect_once(force=True)
