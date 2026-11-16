from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import httpx

from kairos_data.sources.base import (
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
# 沪深京 A 股 5000+ 只；少于此数视为新浪分页中途被限流截断（短页会被当成末页）
_UNIVERSE_MIN = 4000

_KLINE_PERIOD = {"1d": "day", "1w": "week", "1M": "month"}
_KLINE_PAGE = 640  # ifzq 单次日 K 上限（约 800，留余量）


def _is_star(code: str) -> bool:
    """科创板（688xxx，含 689xxx 存托凭证）。symbol 带不带 sh 前缀都行。"""
    return code.removeprefix("sh").startswith("68")


def _lots(code: str, volume: float) -> int:
    """腾讯成交量 → 手。腾讯对科创板给的是股（2026-11-16 实测：行情 f[36] 与日 K 都是，
    与新浪的股数逐日相等），其余板块是手；不统一的话下游 ×100 换算成股会把科创板放大 100 倍。"""
    return round(volume / 100) if _is_star(code) else int(volume)


def _parse_kline_rows(rows: list, symbol: str = "") -> list[Candle]:
    out: list[Candle] = []
    for row in rows:  # [date, open, close, high, low, volume(手；科创板为股), ...]
        out.append(
            Candle(
                ts=datetime.fromisoformat(str(row[0])),
                open=float(row[1]),
                high=float(row[3]),
                low=float(row[4]),
                close=float(row[2]),
                volume=_lots(symbol, float(row[5])),
            )
        )
    return out


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

    def get_universe(self, refresh: bool = False) -> list[StockMeta]:
        if self._universe_cache and not refresh:
            return self._universe_cache
        # 申万官网补缺只在显式刷新时做（每日一次的 refresh_reference），启动后的首轮采集不等它
        industry = self._industry_map(supplement=refresh)
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
                code = str(row["code"])
                out.append(
                    StockMeta(
                        code=code,
                        name=str(row["name"]),
                        market=symbol[:2].upper(),
                        industry=industry.get(code, "—"),
                    )
                )
            if len(rows) < _UNIVERSE_PAGE:
                break
            page += 1
        if len(out) < _UNIVERSE_MIN:
            # 截断的名单不缓存（否则采集器整个进程生命周期只采这几只），
            # 有上次的完整名单就继续用，没有就先凑合本次、下次调用重拉
            return self._universe_cache or out
        self._universe_cache = out
        return out

    def _industry_map(self, supplement: bool = False) -> dict[str, str]:
        """code → 申万二级行业名，via tencent 板块接口（约 124 个板块，一次全量遍历）。

        腾讯的板块成分不含科创板和北交所（2026-11-16 实测 4586 只里一只 688 都没有），
        supplement=True 时再按同一批申万二级代码去申万官网取成分，只补腾讯没给的股票。
        行业分类失败不致命 —— 缺失的股票保持 "—"，下次 refresh 再补。
        """
        out: dict[str, str] = {}
        sw_boards: dict[str, str] = {}  # 申万指数代码（801081）→ 行业名
        try:
            boards = self._get(
                "https://web.ifzq.gtimg.cn/appstock/app/mktHs/rank",
                params={"l": 300, "p": 1, "t": "01/averatio", "o": 0},
            ).json()["data"]
        except Exception:  # noqa: BLE001
            return out
        for b in boards:
            name = str(b["bd_name"]).rstrip("Ⅱ")  # 申万二级的 Ⅱ 后缀对用户是噪音
            board_code = str(b["bd_code"]).removeprefix("pt")
            sw_boards[board_code[-6:]] = name  # pt01801081 → 801081
            offset = 0
            try:
                while True:
                    r = self._get(
                        "https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList",
                        params={
                            "board_code": board_code,
                            "sort_type": "PriceRatio",
                            "direct": "down",
                            "offset": offset,
                            "count": 200,
                        },
                    )
                    data = r.json()["data"]
                    rows = data.get("rank_list") or []
                    for s in rows:
                        out[str(s["code"])[2:]] = name  # sz300359 → 300359
                    offset += len(rows)
                    if not rows or offset >= int(data.get("total", 0)):
                        break
                    time.sleep(0.05)
            except Exception:  # noqa: BLE001 — one bad board must not kill the sweep
                continue
        if supplement:
            for code, name in self._sw_members(sw_boards).items():
                out.setdefault(code, name)
        return out

    def _sw_members(self, boards: dict[str, str]) -> dict[str, str]:
        """申万官网的指数成分：{股票代码: 行业名}。boards 为 {申万指数代码: 行业名}。

        官网证书链不完整（缺中间证书），与行业估值接口一样关掉校验；连续失败 5 个板块就放弃，
        已拿到的照常返回。
        """
        out: dict[str, str] = {}
        misses = 0
        with httpx.Client(timeout=20, headers={"User-Agent": "Mozilla/5.0"}, trust_env=False,
                          verify=False) as client:  # noqa: S501
            for sw_code, name in boards.items():
                try:
                    r = client.get(
                        "https://www.swsresearch.com/institute-sw/api/index_publish/details/component_stocks/",
                        params={"swindexcode": sw_code, "page": 1, "page_size": 10000},
                    )
                    r.raise_for_status()
                    rows = (r.json().get("data") or {}).get("results") or []
                    misses = 0
                except Exception:  # noqa: BLE001
                    misses += 1
                    if misses >= 5:
                        break
                    continue
                for row in rows:
                    code = str(row.get("stockcode") or "")[:6]
                    if len(code) == 6 and code.isdigit():
                        out[code] = name
                time.sleep(0.05)
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
            # Full-quote layout: 3 price, 4 prev_close, 5 open, 30 quote time
            # (yyyymmddHHMMSS), 33 high, 34 low, 36 volume(手；科创板为股), 37 turnover(万元),
            # 38 turnover_rate, 39 PE(TTM), 45 total market cap(亿), 46 PB,
            # 64 dividend yield(%), 65 ROE(%).
            try:
                price = float(f[3])
                if price == 0:  # suspended / untraded
                    continue
                try:
                    exchange_ts = datetime.strptime(f[30], "%Y%m%d%H%M%S")
                except ValueError:
                    exchange_ts = None
                out.append(
                    QuoteData(
                        code=str(f[2]),
                        price=price,
                        prev_close=float(f[4]),
                        open=_f(f[5], price),
                        high=_f(f[33], price),
                        low=_f(f[34], price),
                        volume=_lots(str(f[2]), _f(f[36])),
                        turnover=round(_f(f[37]) / 1e4, 2),
                        turnover_rate=_f(f[38]),
                        pe=_f(f[39]) or None,
                        pb=_f(f[46]),
                        market_cap=round(_f(f[45]), 2),
                        roe=_f(f[65]),
                        dividend_yield=_f(f[64]),
                        ts=now,
                        exchange_ts=exchange_ts,
                    )
                )
            except (ValueError, IndexError):
                continue
        return out

    def get_kline(self, code: str, period: str = "1d", limit: int = 250) -> list[Candle]:
        return self._fetch_kline(_to_symbol(code), period, limit)

    # 指数代码 → 腾讯 symbol（个股的 _to_symbol 规则对指数不适用）
    _INDEX_SYMBOLS = {"000300": "sh000300", "000905": "sh000905", "399006": "sz399006",
                      "000001i": "sh000001", "399001": "sz399001"}

    def get_index_kline(self, code: str, limit: int = 760) -> list[Candle]:
        symbol = self._INDEX_SYMBOLS.get(code)
        if symbol is None:
            return []
        if limit > _KLINE_PAGE:
            return self._kline_paged(symbol, limit)
        return self._fetch_kline(symbol, "1d", limit)

    def get_kline_history(self, code: str, bars: int) -> list[Candle]:
        """深历史日 K（qfq）：按日期向前分页拉取，用于 5 年级回补。

        不回落 Sina——回补要求全段复权口径统一（Sina 未复权），
        失败直接抛给调用方限速重试。
        """
        return self._kline_paged(_to_symbol(code), bars)

    def _kline_paged(self, symbol: str, bars: int) -> list[Candle]:
        out: list[Candle] = []
        end = ""  # 空 = 最新
        while len(out) < bars:
            count = min(_KLINE_PAGE, bars - len(out) + 10)
            r = self._get(
                "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                params={"param": f"{symbol},day,,{end},{count},qfq"},
            )
            data = r.json()["data"][symbol]
            chunk = _parse_kline_rows(data.get("qfqday") or data.get("day") or [], symbol)
            if out:
                chunk = [c for c in chunk if c.ts < out[0].ts]
            if not chunk:
                break
            got = len(chunk)
            out = chunk + out
            if got < count - 10:
                break  # 已到上市日
            end = (out[0].ts - timedelta(days=1)).strftime("%Y-%m-%d")
        return out[-bars:]

    def _fetch_kline(self, symbol: str, period: str, limit: int) -> list[Candle]:
        freq = _KLINE_PERIOD.get(period, "day")
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
        return _parse_kline_rows(data.get(f"qfq{freq}") or data.get(freq) or [], symbol)

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
        # Quote payload carries pe/pb/market_cap/roe/dividend — no extra calls needed.
        out: list[FundamentalData] = []
        for q in self.get_quotes(codes):
            out.append(
                FundamentalData(
                    code=q.code,
                    pe=q.pe,
                    pb=q.pb,
                    roe=q.roe,
                    market_cap=q.market_cap,
                    # DSL 里股息率约定为小数（0.03 = 3%），行情给的是百分数
                    dividend_yield=round(q.dividend_yield / 100, 4),
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
