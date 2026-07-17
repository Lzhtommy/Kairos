from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.services.market import factor_rows, index_dicts, stock_dicts

router = APIRouter(tags=["market"])


@router.get("/quotes")
def quotes(codes: str | None = Query(default=None), db: Session = Depends(get_db)):
    code_list = [c.strip() for c in codes.split(",") if c.strip()] if codes else None
    return stock_dicts(db, code_list)


@router.get("/indices")
def indices(db: Session = Depends(get_db)):
    return index_dicts(db)


@router.get("/quotes/ranking")
def ranking(
    type: str = Query(default="gainers"),
    limit: int = Query(default=20),
    db: Session = Depends(get_db),
):
    rows = factor_rows(db)
    if type == "active":
        rows.sort(key=lambda r: r["turnover"], reverse=True)
    else:  # gainers
        rows.sort(key=lambda r: r["change_pct"], reverse=True)
    codes = [r["code"] for r in rows[:limit]]
    items = stock_dicts(db, codes)
    order = {c: i for i, c in enumerate(codes)}
    items.sort(key=lambda s: order.get(s["code"], 0))
    return items


@router.get("/kline")
def kline(code: str, period: str = "1d", limit: int = 250, db: Session = Depends(get_db)):
    from sqlalchemy import select

    from app.models.market import Kline

    rows = db.execute(
        select(Kline)
        .where(Kline.code == code, Kline.period == period)
        .order_by(Kline.ts.asc())
        .limit(limit)
    ).scalars().all()
    return [
        {
            "t": k.ts.strftime("%Y-%m-%d"),
            "open": k.open,
            "high": k.high,
            "low": k.low,
            "close": k.close,
            "volume": k.volume,
        }
        for k in rows
    ]
