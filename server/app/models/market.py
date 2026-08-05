from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StockInfo(Base):
    """Static per-stock metadata (the股票池)."""

    __tablename__ = "stock_info"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    market: Mapped[str] = mapped_column(String(8))  # SH / SZ
    industry: Mapped[str] = mapped_column(String(64))


class Quote(Base):
    """Latest snapshot per stock (upserted every minute)."""

    __tablename__ = "quotes"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    price: Mapped[float] = mapped_column(Float)
    prev_close: Mapped[float] = mapped_column(Float)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(Integer)
    turnover: Mapped[float] = mapped_column(Float)          # 亿元
    turnover_rate: Mapped[float] = mapped_column(Float)     # %
    pe: Mapped[float | None] = mapped_column(Float, nullable=True)
    pb: Mapped[float] = mapped_column(Float)
    market_cap: Mapped[float] = mapped_column(Float)        # 亿元
    roe: Mapped[float] = mapped_column(Float)


class Kline(Base):
    __tablename__ = "kline"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    period: Mapped[str] = mapped_column(String(8), default="1d")
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(Integer)


class AdjustmentEvent(Base):
    """除权除息事件：ex-date 当日检测到的复权比例，历史 bar 已按此重标定。

    ratio = 交易所披露的调整后昨收 / 库里昨收（分红/送转 < 1）。
    留档用于审计与季度 kline-full 重刷的核对；(code, date) 唯一。
    """

    __tablename__ = "adjustment_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ratio: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class IndexQuote(Base):
    __tablename__ = "index_quotes"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    price: Mapped[float] = mapped_column(Float)
    change: Mapped[float] = mapped_column(Float)
    change_pct: Mapped[float] = mapped_column(Float)


class FactorSnapshot(Base):
    """每个交易日收盘后的全市场因子快照——point-in-time 回测的数据基础。

    quotes 表只有"最新一份"，历史因子只能靠逐日归档；从部署日起积累，
    积累越久，无前视回测的可用窗口越长。约 5500 行/交易日。
    """

    __tablename__ = "factor_history"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, index=True)
    pe: Mapped[float | None] = mapped_column(Float, nullable=True)
    pb: Mapped[float] = mapped_column(Float)
    roe: Mapped[float] = mapped_column(Float)
    turnover_rate: Mapped[float] = mapped_column(Float)
    turnover: Mapped[float] = mapped_column(Float)          # 亿元
    market_cap: Mapped[float] = mapped_column(Float)        # 亿元
    change_pct: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    dividend_yield: Mapped[float] = mapped_column(Float, default=0.0)
    industry: Mapped[str] = mapped_column(String(64), default="—")


class Fundamental(Base):
    __tablename__ = "fundamentals"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    pe: Mapped[float | None] = mapped_column(Float, nullable=True)
    pb: Mapped[float] = mapped_column(Float)
    roe: Mapped[float] = mapped_column(Float)
    market_cap: Mapped[float] = mapped_column(Float)
    dividend_yield: Mapped[float] = mapped_column(Float, default=0.0)
