"""共享夹具：内存 SQLite + 合成 K 线/快照数据构造器。

不碰真实 kairos.db，也不依赖网络——backtest 的基准函数在用例里 monkeypatch。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.models.market import Kline, Quote, StockInfo


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def trading_days(start: str, count: int) -> list[datetime]:
    """从 start 起的 count 个工作日（跳过周末，近似交易日历）。"""
    out: list[datetime] = []
    d = datetime.fromisoformat(start)
    while len(out) < count:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def add_stock(
    db: Session,
    code: str,
    days: list[datetime],
    closes: list[float],
    opens: list[float] | None = None,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[int] | None = None,
    name: str = "",
    industry: str = "白酒",
    pe: float | None = 10.0,
    roe: float = 15.0,
    market_cap: float = 100.0,
) -> None:
    """一只股票的静态信息 + 最新快照 + 全段日 K。"""
    assert len(days) == len(closes)
    opens = opens or closes
    highs = highs or [max(o, c) for o, c in zip(opens, closes)]
    lows = lows or [min(o, c) for o, c in zip(opens, closes)]
    volumes = volumes or [10_000] * len(days)
    db.add(StockInfo(code=code, name=name or f"股票{code}", market="SH", industry=industry))
    db.add(
        Quote(
            code=code,
            ts=days[-1],
            price=closes[-1],
            prev_close=closes[-2] if len(closes) > 1 else closes[-1],
            open=opens[-1],
            high=highs[-1],
            low=lows[-1],
            volume=volumes[-1],
            turnover=1.0,
            turnover_rate=1.0,
            pe=pe,
            pb=2.0,
            market_cap=market_cap,
            roe=roe,
        )
    )
    for d, o, h, lo, c, v in zip(days, opens, highs, lows, closes, volumes):
        db.add(Kline(code=code, period="1d", ts=d, open=o, high=h, low=lo, close=c, volume=v))
    db.commit()
