from datetime import datetime

from sqlalchemy import func, select

from app.core.db import upsert_rows
from app.models.market import Fundamental, Quote


def _quote(code: str, price: float) -> dict:
    return {
        "code": code, "ts": datetime(2026, 11, 5, 10, 0), "price": price, "prev_close": 10.0,
        "open": 10.0, "high": price, "low": 10.0, "volume": 3_000_000_000,
        "turnover": 1.0, "turnover_rate": 1.0, "pe": None, "pb": 1.0,
        "market_cap": 123456.789012, "roe": 5.0,
    }


def test_upsert_inserts_then_updates(db):
    upsert_rows(db, Quote, [_quote("600000", 10.5), _quote("600001", 11.0)])
    db.commit()
    upsert_rows(db, Quote, [_quote("600001", 12.0), _quote("600002", 9.0)], batch=1)
    db.commit()

    prices = dict(db.execute(select(Quote.code, Quote.price)).all())
    assert prices == {"600000": 10.5, "600001": 12.0, "600002": 9.0}
    q = db.get(Quote, "600001")
    # 超出 INT 的成交量、双精度市值在两种库上都原样保留
    assert q.volume == 3_000_000_000
    assert q.market_cap == 123456.789012


def test_upsert_empty_is_noop(db):
    upsert_rows(db, Fundamental, [])
    assert db.execute(select(func.count()).select_from(Fundamental)).scalar() == 0
