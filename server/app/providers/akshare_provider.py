from __future__ import annotations

from datetime import datetime, timezone

from app.providers.base import (
    Candle,
    FundamentalData,
    IndexData,
    QuoteData,
    StockMeta,
)

# Indices we surface on the dashboard.
_INDEX_MAP = {
    "000001": "上证指数",
    "399001": "深证成指",
    "399006": "创业板指",
    "000688": "科创50",
    "000300": "沪深300",
}


class AkShareProvider:
    """Real A-share data via AkShare. Requires outbound access to the data endpoints.

    All akshare calls are lazy-imported so the app can run without the package
    installed; the factory falls back to SeedProvider when this provider can't init.
    """

    name = "akshare"

    def __init__(self) -> None:
        import akshare  # noqa: F401  (raises ImportError → factory falls back)

        self._ak = akshare
        self._spot_cache = None

    def _spot(self):
        # 东财实时行情快照（全 A 股）
        return self._ak.stock_zh_a_spot_em()

    def get_universe(self, refresh: bool = False) -> list[StockMeta]:
        df = self._spot()
        out: list[StockMeta] = []
        for _, r in df.iterrows():
            code = str(r["代码"])
            market = "SH" if code.startswith(("6", "5")) else "SZ"
            out.append(StockMeta(code=code, name=str(r["名称"]), market=market, industry="—"))
        return out

    def get_quotes(self, codes: list[str] | None = None) -> list[QuoteData]:
        df = self._spot()
        if codes:
            df = df[df["代码"].astype(str).isin(codes)]
        out: list[QuoteData] = []
        now = datetime.now(timezone.utc)
        for _, r in df.iterrows():
            try:
                price = float(r["最新价"])
                pct = float(r["涨跌幅"])
                prev_close = round(price / (1 + pct / 100), 2) if pct != -100 else price
                pe = float(r["市盈率-动态"]) if r["市盈率-动态"] not in (None, "-", "") else None
                out.append(
                    QuoteData(
                        code=str(r["代码"]),
                        price=price,
                        prev_close=prev_close,
                        open=float(r["今开"]),
                        high=float(r["最高"]),
                        low=float(r["最低"]),
                        volume=int(float(r["成交量"])),
                        turnover=round(float(r["成交额"]) / 1e8, 2),
                        turnover_rate=float(r["换手率"]),
                        pe=pe,
                        pb=float(r["市净率"]),
                        market_cap=round(float(r["总市值"]) / 1e8, 2),
                        roe=0.0,  # not in spot; enriched by fundamentals
                        ts=now,
                    )
                )
            except (ValueError, KeyError, TypeError):
                continue
        return out

    def get_kline(self, code: str, period: str = "1d", limit: int = 250) -> list[Candle]:
        df = self._ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
        df = df.tail(limit)
        out: list[Candle] = []
        for _, r in df.iterrows():
            out.append(
                Candle(
                    ts=datetime.fromisoformat(str(r["日期"])),
                    open=float(r["开盘"]),
                    high=float(r["最高"]),
                    low=float(r["最低"]),
                    close=float(r["收盘"]),
                    volume=int(float(r["成交量"])),
                )
            )
        return out

    def get_fundamentals(self, codes: list[str] | None = None) -> list[FundamentalData]:
        # Spot snapshot carries pe/pb/market_cap; roe/dividend need extra calls (skipped in MVP).
        out: list[FundamentalData] = []
        for q in self.get_quotes(codes):
            out.append(
                FundamentalData(
                    code=q.code,
                    pe=q.pe,
                    pb=q.pb,
                    roe=q.roe,
                    market_cap=q.market_cap,
                    dividend_yield=0.0,
                )
            )
        return out

    def get_indices(self) -> list[IndexData]:
        df = self._ak.stock_zh_index_spot_em()
        out: list[IndexData] = []
        for _, r in df.iterrows():
            code = str(r["代码"])
            if code not in _INDEX_MAP:
                continue
            out.append(
                IndexData(
                    code=code,
                    name=_INDEX_MAP[code],
                    price=float(r["最新价"]),
                    change=float(r["涨跌额"]),
                    change_pct=float(r["涨跌幅"]),
                )
            )
        return out
