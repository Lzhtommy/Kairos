from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol, runtime_checkable


@dataclass
class StockMeta:
    code: str
    name: str
    market: str
    industry: str


@dataclass
class QuoteData:
    code: str
    price: float
    prev_close: float
    open: float
    high: float
    low: float
    volume: int
    turnover: float
    turnover_rate: float
    pe: float | None
    pb: float
    market_cap: float
    roe: float
    dividend_yield: float = 0.0  # percent, as quoted (3.9 = 3.9%)
    ts: datetime | None = None
    # 交易所行情时间戳（CST，naive）。用于判断行情属于哪个交易日——
    # 节假日/停牌时腾讯返回的是旧数据，靠它避免合成错误的日 K。
    exchange_ts: datetime | None = None


@dataclass
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class IndexData:
    code: str
    name: str
    price: float
    change: float
    change_pct: float


@dataclass
class FundamentalData:
    code: str
    pe: float | None
    pb: float
    roe: float
    market_cap: float
    dividend_yield: float = 0.0


@runtime_checkable
class DataProvider(Protocol):
    name: str

    def get_universe(self, refresh: bool = False) -> list[StockMeta]: ...

    def get_quotes(self, codes: list[str] | None = None) -> list[QuoteData]: ...

    def get_kline(self, code: str, period: str = "1d", limit: int = 250) -> list[Candle]: ...

    def get_fundamentals(self, codes: list[str] | None = None) -> list[FundamentalData]: ...

    def get_indices(self) -> list[IndexData]: ...
