"""Data collector: seeds reference data once, refreshes quotes/indices each tick.

Runs every `collect_interval_seconds` via APScheduler (see main.py). With a
live provider (akshare/tencent) it only collects during A-share trading
sessions; with the seed provider it always refreshes so the demo keeps moving.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from time import sleep

from sqlalchemy import delete, func, select

from app.core.db import SessionLocal
from app.models.market import Fundamental, IndexQuote, Kline, Quote, StockInfo
from app.providers.factory import get_provider

logger = logging.getLogger("kairos.collector")

# China Standard Time = UTC+8; trading sessions 09:30–11:30 and 13:00–15:00.
_CST_OFFSET_HOURS = 8


def in_trading_session(now_utc: datetime | None = None) -> bool:
    now = now_utc or datetime.now(timezone.utc)
    cst_hour = (now.hour + _CST_OFFSET_HOURS) % 24
    t = time(cst_hour, now.minute)
    if now.weekday() >= 5:  # Sat/Sun
        return False
    return (time(9, 30) <= t <= time(11, 30)) or (time(13, 0) <= t <= time(15, 0))


def _refresh_quotes(db) -> int:
    provider = get_provider()
    quotes = provider.get_quotes()
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
                candles = provider.get_kline(code, "1d", 250)
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
                candles = provider.get_kline(code, "1d", 250)
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
        logger.info(
            "Reference refresh: industry for %d stocks, fundamentals updated, klines backfilled for %d new stocks",
            n_ind, backfilled,
        )
    except Exception as exc:  # noqa: BLE001 — keep the scheduler alive
        logger.warning("Reference refresh failed: %s", exc)
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
