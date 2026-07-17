from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from app.providers.base import (
    Candle,
    FundamentalData,
    IndexData,
    QuoteData,
    StockMeta,
)

# (code, name, market, industry, base_price, market_cap 亿元) — mirrors src/lib/mock-data.ts
RAW: list[tuple[str, str, str, str, float, float]] = [
    ("600519", "贵州茅台", "SH", "白酒", 1682.0, 21100),
    ("300750", "宁德时代", "SZ", "新能源", 187.35, 8230),
    ("601318", "中国平安", "SH", "银行", 47.82, 8760),
    ("000858", "五粮液", "SZ", "白酒", 128.6, 4990),
    ("600036", "招商银行", "SH", "银行", 34.5, 8690),
    ("002594", "比亚迪", "SZ", "汽车", 251.8, 7330),
    ("601899", "紫金矿业", "SH", "军工", 16.42, 4310),
    ("688981", "中芯国际", "SH", "半导体", 82.6, 6510),
    ("000333", "美的集团", "SZ", "家电", 68.9, 4820),
    ("600809", "山西汾酒", "SH", "白酒", 178.4, 2180),
    ("300760", "迈瑞医疗", "SZ", "医药", 226.5, 2740),
    ("601888", "中国中免", "SH", "食品饮料", 62.3, 1290),
    ("000725", "京东方A", "SZ", "半导体", 4.12, 1420),
    ("600030", "中信证券", "SH", "券商", 24.6, 1860),
    ("002415", "海康威视", "SZ", "半导体", 29.8, 2790),
    ("300059", "东方财富", "SZ", "券商", 15.3, 2380),
    ("601012", "隆基绿能", "SH", "新能源", 18.9, 1430),
    ("600276", "恒瑞医药", "SH", "医药", 45.7, 2920),
    ("002230", "科大讯飞", "SZ", "半导体", 42.1, 970),
    ("601166", "兴业银行", "SH", "银行", 17.8, 3690),
    # —— 扩充演示股票池（行业均在筛选下拉列表内）——
    ("601398", "工商银行", "SH", "银行", 6.12, 21800),
    ("601939", "建设银行", "SH", "银行", 7.85, 19600),
    ("601288", "农业银行", "SH", "银行", 4.36, 15200),
    ("601988", "中国银行", "SH", "银行", 4.72, 13900),
    ("600000", "浦发银行", "SH", "银行", 8.9, 2610),
    ("601328", "交通银行", "SH", "银行", 7.34, 5450),
    ("000568", "泸州老窖", "SZ", "白酒", 168.5, 2480),
    ("002304", "洋河股份", "SZ", "白酒", 92.3, 1390),
    ("000799", "酒鬼酒", "SZ", "白酒", 58.7, 190),
    ("603369", "今世缘", "SH", "白酒", 46.2, 580),
    ("601688", "华泰证券", "SH", "券商", 16.8, 1520),
    ("000776", "广发证券", "SZ", "券商", 15.6, 1190),
    ("601211", "国泰君安", "SH", "券商", 15.9, 1410),
    ("600837", "海通证券", "SH", "券商", 9.4, 1230),
    ("002460", "赣锋锂业", "SZ", "新能源", 38.6, 780),
    ("300014", "亿纬锂能", "SZ", "新能源", 44.2, 900),
    ("688599", "天合光能", "SH", "新能源", 22.1, 480),
    ("300274", "阳光电源", "SZ", "新能源", 88.5, 1310),
    ("603501", "韦尔股份", "SH", "半导体", 98.4, 1200),
    ("688012", "中微公司", "SH", "半导体", 148.6, 920),
    ("002049", "紫光国微", "SZ", "半导体", 72.3, 610),
    ("603986", "兆易创新", "SH", "半导体", 105.2, 700),
    ("300347", "泰格医药", "SZ", "医药", 58.9, 520),
    ("600196", "复星医药", "SH", "医药", 26.4, 700),
    ("000538", "云南白药", "SZ", "医药", 54.1, 970),
    ("300759", "康龙化成", "SZ", "医药", 28.7, 510),
    ("600760", "中航沈飞", "SH", "军工", 42.5, 1170),
    ("600893", "航发动力", "SH", "军工", 38.9, 1030),
    ("000768", "中航西飞", "SZ", "军工", 25.3, 700),
    ("002013", "中航机电", "SZ", "军工", 12.6, 460),
    ("600104", "上汽集团", "SH", "汽车", 14.8, 1710),
    ("000625", "长安汽车", "SZ", "汽车", 13.2, 1310),
    ("601633", "长城汽车", "SH", "汽车", 24.1, 2050),
    ("601238", "广汽集团", "SH", "汽车", 8.7, 910),
    ("000651", "格力电器", "SZ", "家电", 38.4, 2160),
    ("600690", "海尔智家", "SH", "家电", 27.8, 2600),
    ("002508", "老板电器", "SZ", "家电", 21.5, 200),
    ("000921", "海信家电", "SZ", "家电", 28.3, 390),
    ("603288", "海天味业", "SH", "食品饮料", 41.2, 2290),
    ("600887", "伊利股份", "SH", "食品饮料", 28.6, 1820),
    ("000895", "双汇发展", "SZ", "食品饮料", 25.9, 900),
    ("603605", "珀莱雅", "SH", "食品饮料", 98.7, 390),
]

INDICES_RAW = [
    ("000001", "上证指数", 3187.42, 12.86, 0.41),
    ("399001", "深证成指", 10203.71, -34.52, -0.34),
    ("399006", "创业板指", 2094.33, 18.07, 0.87),
    ("000688", "科创50", 1012.55, -6.21, -0.61),
    ("000300", "沪深300", 3821.09, 9.44, 0.25),
]


def _mulberry32(seed: int):
    s = seed & 0xFFFFFFFF

    def rand() -> float:
        nonlocal s
        s = (s + 0x6D2B79F5) & 0xFFFFFFFF
        t = s
        t = (t ^ (t >> 15)) * (t | 1) & 0xFFFFFFFF
        t ^= (t + ((t ^ (t >> 7)) * (t | 61) & 0xFFFFFFFF)) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296

    return rand


def _hash_code(text: str) -> int:
    h = 2166136261
    for ch in text:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h & 0xFFFFFFFF


class SeedProvider:
    """Deterministic synthetic data — self-contained, no network needed."""

    name = "seed"

    def get_universe(self) -> list[StockMeta]:
        return [StockMeta(code=c, name=n, market=m, industry=ind) for c, n, m, ind, *_ in RAW]

    def _base_quote(self, row, i: int) -> QuoteData:
        code, _name, _market, _industry, price, market_cap = row
        rand = _mulberry32(_hash_code(code) + i)
        change_pct = (rand() - 0.48) * 6
        prev_close = round(price / (1 + change_pct / 100), 2)
        high = round(price * (1 + rand() * 0.015), 2)
        low = round(price * (1 - rand() * 0.015), 2)
        pe = round(rand() * 55 + 8, 1) if rand() > 0.15 else None
        return QuoteData(
            code=code,
            price=price,
            prev_close=prev_close,
            open=round(prev_close * (1 + (rand() - 0.5) * 0.01), 2),
            high=high,
            low=low,
            volume=int(rand() * 800000 + 20000),
            turnover=round(rand() * 40 + 0.5, 2),
            turnover_rate=round(rand() * 4 + 0.1, 2),
            pe=pe,
            pb=round(rand() * 8 + 0.8, 2),
            market_cap=market_cap,
            roe=round(rand() * 28 + 2, 1),
        )

    def get_quotes(self, codes: list[str] | None = None) -> list[QuoteData]:
        # Per-minute deterministic jitter so the "live" price moves each minute.
        minute_seed = int(datetime.now(timezone.utc).timestamp() // 60)
        out: list[QuoteData] = []
        for i, row in enumerate(RAW):
            code = row[0]
            if codes and code not in codes:
                continue
            q = self._base_quote(row, i)
            jr = _mulberry32(_hash_code(code) + minute_seed)
            drift = (jr() - 0.5) * q.price * 0.006
            q.price = round(q.price + drift, 2)
            q.high = max(q.high, q.price)
            q.low = min(q.low, q.price)
            q.ts = datetime.now(timezone.utc)
            out.append(q)
        return out

    def get_kline(self, code: str, period: str = "1d", limit: int = 250) -> list[Candle]:
        row = next((r for r in RAW if r[0] == code), None)
        if row is None:
            return []
        base = row[4]
        rand = _mulberry32(_hash_code(code) + 99)
        # Build a random-walk series ending near `base`, then walk backwards in time.
        closes: list[float] = []
        v = base
        for _ in range(limit):
            closes.append(v)
            v = v / (1 + (rand() - 0.5) * 0.03)  # step backwards
        closes.reverse()
        candles: list[Candle] = []
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        n = len(closes)
        for idx, close in enumerate(closes):
            ts = today - timedelta(days=(n - 1 - idx))
            prev = closes[idx - 1] if idx > 0 else close
            open_ = round(prev * (1 + (rand() - 0.5) * 0.01), 2)
            high = round(max(open_, close) * (1 + rand() * 0.012), 2)
            low = round(min(open_, close) * (1 - rand() * 0.012), 2)
            candles.append(
                Candle(
                    ts=ts,
                    open=open_,
                    high=high,
                    low=low,
                    close=round(close, 2),
                    volume=int(rand() * 800000 + 20000),
                )
            )
        return candles

    def get_fundamentals(self, codes: list[str] | None = None) -> list[FundamentalData]:
        out: list[FundamentalData] = []
        for i, row in enumerate(RAW):
            code = row[0]
            if codes and code not in codes:
                continue
            q = self._base_quote(row, i)
            rand = _mulberry32(_hash_code(code) + 7)
            out.append(
                FundamentalData(
                    code=code,
                    pe=q.pe,
                    pb=q.pb,
                    roe=q.roe,
                    market_cap=q.market_cap,
                    dividend_yield=round(rand() * 0.06, 4),
                )
            )
        return out

    def get_indices(self) -> list[IndexData]:
        minute_seed = int(datetime.now(timezone.utc).timestamp() // 60)
        out: list[IndexData] = []
        for code, name, price, change, _pct in INDICES_RAW:
            jr = _mulberry32(_hash_code(code) + minute_seed)
            drift = (jr() - 0.5) * price * 0.002
            new_price = round(price + drift, 2)
            new_change = round(change + drift, 2)
            denom = new_price - new_change
            new_pct = round((new_change / denom) * 100, 2) if denom else 0.0
            out.append(
                IndexData(code=code, name=name, price=new_price, change=new_change, change_pct=new_pct)
            )
        return out
