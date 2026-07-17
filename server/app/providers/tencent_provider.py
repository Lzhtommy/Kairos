from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx

from app.providers.base import (
    Candle,
    FundamentalData,
    IndexData,
    QuoteData,
    StockMeta,
)

# Indices we surface on the dashboard (code → tencent symbol, display name).
_INDEX_MAP = {
    "000001": ("s_sh000001", "上证指数"),
    "399001": ("s_sz399001", "深证成指"),
    "399006": ("s_sz399006", "创业板指"),
    "000688": ("s_sh000688", "科创50"),
    "000300": ("s_sh000300", "沪深300"),
}

_QUOTE_BATCH = 80
_UNIVERSE_PAGE = 100

_KLINE_PERIOD = {"1d": "day", "1w": "week", "1M": "month"}


def _to_symbol(code: str) -> str:
    """Bare code → exchange-prefixed symbol (600519 → sh600519)."""
    if code.startswith(("6", "5")):
        return f"sh{code}"
    if code.startswith(("4", "8", "9")):
        return f"bj{code}"
    return f"sz{code}"


def _f(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


class TencentProvider:
    """Real A-share data via Tencent/Sina public quote endpoints.

    Alternative to AkShareProvider for networks where the EastMoney endpoints
    are blocked or rate-limited: quotes/klines come from qt.gtimg.cn and
    web.ifzq.gtimg.cn (batched, no pagination against a single host), the
    stock universe from Sina's market-center list.
    """

    name = "tencent"

    def __init__(self) -> None:
        self._client = httpx.Client(
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
            trust_env=False,  # ignore proxy env vars; these hosts are direct-reachable
        )
        self._universe_cache: list[StockMeta] | None = None
        self._ifzq_down_until = 0.0  # circuit breaker for the kline host

    def _get(self, url: str, **kwargs) -> httpx.Response:
        """GET with retry — the quote endpoints throw sporadic 5xx under sustained load."""
        last: Exception | None = None
        for attempt in range(3):
            try:
                r = self._client.get(url, **kwargs)
                r.raise_for_status()
                return r
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last = exc
                time.sleep(1 + attempt * 2)
        raise last  # type: ignore[misc]

    def get_universe(self) -> list[StockMeta]:
        if self._universe_cache:
            return self._universe_cache
        out: list[StockMeta] = []
        page = 1
        while True:
            r = self._get(
                "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData",
                params={"page": page, "num": _UNIVERSE_PAGE, "sort": "symbol", "asc": 1, "node": "hs_a"},
                headers={"Referer": "https://finance.sina.com.cn"},
            )
            rows = r.json() or []
            for row in rows:
                symbol = str(row["symbol"])  # e.g. sh600519
                out.append(
                    StockMeta(
                        code=str(row["code"]),
                        name=str(row["name"]),
                        market=symbol[:2].upper(),
                        industry="—",
                    )
                )
            if len(rows) < _UNIVERSE_PAGE:
                break
            page += 1
        self._universe_cache = out
        return out

    def _fetch_quote_fields(self, symbols: list[str]) -> dict[str, list[str]]:
        """Batch-fetch qt.gtimg.cn full quotes → {symbol: tilde-split fields}."""
        out: dict[str, list[str]] = {}
        for i in range(0, len(symbols), _QUOTE_BATCH):
            batch = symbols[i : i + _QUOTE_BATCH]
            r = self._get(f"https://qt.gtimg.cn/q={','.join(batch)}")
            for line in r.content.decode("gbk", errors="ignore").splitlines():
                if "=" not in line:
                    continue
                head, _, body = line.partition("=")
                out[head.strip().removeprefix("v_")] = body.strip().strip(';"').split("~")
        return out

    def get_quotes(self, codes: list[str] | None = None) -> list[QuoteData]:
        if codes is None:
            codes = [m.code for m in self.get_universe()]
        fields = self._fetch_quote_fields([_to_symbol(c) for c in codes])
        now = datetime.now(timezone.utc)
        out: list[QuoteData] = []
        for f in fields.values():
            # Full-quote layout: 3 price, 4 prev_close, 5 open, 33 high, 34 low,
            # 36 volume(手), 37 turnover(万元), 38 turnover_rate, 39 PE(TTM),
            # 45 total market cap(亿), 46 PB.
            try:
                price = float(f[3])
                if price == 0:  # suspended / untraded
                    continue
                out.append(
                    QuoteData(
                        code=str(f[2]),
                        price=price,
                        prev_close=float(f[4]),
                        open=_f(f[5], price),
                        high=_f(f[33], price),
                        low=_f(f[34], price),
                        volume=int(_f(f[36])),
                        turnover=round(_f(f[37]) / 1e4, 2),
                        turnover_rate=_f(f[38]),
                        pe=_f(f[39]) or None,
                        pb=_f(f[46]),
                        market_cap=round(_f(f[45]), 2),
                        roe=0.0,  # not in quote; enriched by fundamentals
                        ts=now,
                    )
                )
            except (ValueError, IndexError):
                continue
        return out

    def get_kline(self, code: str, period: str = "1d", limit: int = 250) -> list[Candle]:
        freq = _KLINE_PERIOD.get(period, "day")
        symbol = _to_symbol(code)
        if freq == "day" and time.monotonic() < self._ifzq_down_until:
            return self._kline_sina(symbol, limit)
        try:
            r = self._get(
                "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                params={"param": f"{symbol},{freq},,,{limit},qfq"},
            )
        except httpx.HTTPError:
            # ifzq rate-limits sustained bulk fetching (e.g. the kline bootstrap)
            # with persistent 501s; Sina tolerates the same volume. Trip the
            # breaker so the remaining bulk calls skip the doomed retries.
            if freq != "day":
                raise
            self._ifzq_down_until = time.monotonic() + 600
            return self._kline_sina(symbol, limit)
        data = r.json()["data"][symbol]
        rows = data.get(f"qfq{freq}") or data.get(freq) or []
        out: list[Candle] = []
        for row in rows:  # [date, open, close, high, low, volume(手), ...]
            out.append(
                Candle(
                    ts=datetime.fromisoformat(str(row[0])),
                    open=float(row[1]),
                    high=float(row[3]),
                    low=float(row[4]),
                    close=float(row[2]),
                    volume=int(float(row[5])),
                )
            )
        return out

    def _kline_sina(self, symbol: str, limit: int) -> list[Candle]:
        """Daily kline via Sina. Unadjusted (no qfq) — close enough for the MVP screener."""
        r = self._get(
            "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData",
            params={"symbol": symbol, "scale": 240, "ma": "no", "datalen": limit},
            headers={"Referer": "https://finance.sina.com.cn"},
        )
        out: list[Candle] = []
        for row in r.json() or []:
            out.append(
                Candle(
                    ts=datetime.fromisoformat(str(row["day"])),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(float(row["volume"])) // 100,  # 股 → 手, matching tencent/akshare
                )
            )
        return out

    def get_fundamentals(self, codes: list[str] | None = None) -> list[FundamentalData]:
        # Quote payload carries pe/pb/market_cap; roe/dividend need extra calls (skipped in MVP).
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
        symbols = [sym for sym, _ in _INDEX_MAP.values()]
        fields = self._fetch_quote_fields(symbols)
        out: list[IndexData] = []
        for code, (sym, name) in _INDEX_MAP.items():
            f = fields.get(sym)
            if not f:
                continue
            # Simplified (s_) layout: 3 price, 4 change, 5 change_pct.
            out.append(
                IndexData(
                    code=code,
                    name=name,
                    price=float(f[3]),
                    change=float(f[4]),
                    change_pct=float(f[5]),
                )
            )
        return out
