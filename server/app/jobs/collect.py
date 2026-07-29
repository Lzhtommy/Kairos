"""Data collector: seeds reference data once, refreshes quotes/indices each tick.

Runs every `collect_interval_seconds` via APScheduler (see main.py). With a
live provider (akshare/tencent) it only collects during A-share trading
sessions; with the seed provider it always refreshes so the demo keeps moving.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timezone
from time import sleep

from sqlalchemy import func, select

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
    db.commit()
    return len(quotes)


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
            if meta.industry == "—":
                continue
            info = db.get(StockInfo, meta.code)
            if info is None:
                db.add(StockInfo(code=meta.code, name=meta.name, market=meta.market, industry=meta.industry))
            elif info.industry != meta.industry:
                info.industry = meta.industry
            n_ind += 1
        for f in provider.get_fundamentals():
            db.merge(
                Fundamental(
                    code=f.code, pe=f.pe, pb=f.pb, roe=f.roe,
                    market_cap=f.market_cap, dividend_yield=f.dividend_yield,
                )
            )
        db.commit()
        logger.info("Reference refresh: industry for %d stocks, fundamentals updated", n_ind)
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
