"""Read helpers that assemble market data from the DB into API/DSL shapes."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.market import Fundamental, IndexQuote, Kline, Quote, StockInfo


def _sparks(db: Session, codes: list[str] | None = None, points: int = 20) -> dict[str, list[float]]:
    """Last `points` closes per code in ONE window-function query — a per-code
    query here turns the full /quotes response into 5000+ round trips."""
    rn = func.row_number().over(partition_by=Kline.code, order_by=Kline.ts.desc()).label("rn")
    sub = select(Kline.code, Kline.close, Kline.ts, rn).where(Kline.period == "1d")
    if codes:
        sub = sub.where(Kline.code.in_(codes))
    sub = sub.subquery()
    stmt = (
        select(sub.c.code, sub.c.close)
        .where(sub.c.rn <= points)
        .order_by(sub.c.code, sub.c.ts.asc())
    )
    out: dict[str, list[float]] = {}
    for code, close in db.execute(stmt):
        out.setdefault(code, []).append(round(close, 2))
    return out


def change_pct(price: float, prev_close: float) -> float:
    if not prev_close:
        return 0.0
    return round((price - prev_close) / prev_close * 100, 2)


def stock_dicts(db: Session, codes: list[str] | None = None) -> list[dict[str, Any]]:
    """Frontend `Stock` shape (camelCase) for /quotes, /screener, /watchlist.

    codes=None → 全市场；codes=[] → 空结果（零命中的策略不能退化成全市场）。
    """
    if codes is not None and not codes:
        return []
    info = {s.code: s for s in db.execute(select(StockInfo)).scalars().all()}
    stmt = select(Quote)
    if codes:
        stmt = stmt.where(Quote.code.in_(codes))
    sparks = _sparks(db, codes)
    out: list[dict[str, Any]] = []
    for q in db.execute(stmt).scalars().all():
        meta = info.get(q.code)
        out.append(
            {
                "code": q.code,
                "name": meta.name if meta else q.code,
                "market": meta.market if meta else "SH",
                "industry": meta.industry if meta else "—",
                "price": q.price,
                "prevClose": q.prev_close,
                "open": q.open,
                "high": q.high,
                "low": q.low,
                "volume": q.volume,
                "turnover": q.turnover,
                "turnoverRate": q.turnover_rate,
                "pe": q.pe,
                "pb": q.pb,
                "marketCap": q.market_cap,
                "roe": q.roe,
                "spark": sparks.get(q.code, []),
            }
        )
    return out


def factor_rows(db: Session, codes: list[str] | None = None) -> list[dict[str, Any]]:
    """Rows carrying every whitelisted factor value — input to the DSL engine."""
    if codes is not None and not codes:
        return []
    info = {s.code: s for s in db.execute(select(StockInfo)).scalars().all()}
    funds = {f.code: f for f in db.execute(select(Fundamental)).scalars().all()}
    stmt = select(Quote)
    if codes:
        stmt = stmt.where(Quote.code.in_(codes))
    rows: list[dict[str, Any]] = []
    for q in db.execute(stmt).scalars().all():
        meta = info.get(q.code)
        fund = funds.get(q.code)
        rows.append(
            {
                "code": q.code,
                "name": meta.name if meta else q.code,
                "market": meta.market if meta else "SH",
                "industry": meta.industry if meta else "—",
                "pe": q.pe,
                "pb": q.pb,
                "roe": q.roe,
                "turnover_rate": q.turnover_rate,
                "turnover": q.turnover,
                "market_cap": q.market_cap,
                "change_pct": change_pct(q.price, q.prev_close),
                "price": q.price,
                "dividend_yield": fund.dividend_yield if fund else 0.0,
            }
        )
    return rows


def index_dicts(db: Session) -> list[dict[str, Any]]:
    out = []
    for i in db.execute(select(IndexQuote)).scalars().all():
        out.append(
            {
                "code": i.code,
                "name": i.name,
                "price": i.price,
                "change": i.change,
                "changePct": i.change_pct,
            }
        )
    return out
