from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import httpx

from app.providers.base import (
    Candle,
    FundamentalData,
    IndexData,
    QuoteData,
    StockMeta,
)

# 行情走 push2delay（延时快照）：push2 实时集群对同 IP 连续请求有风控
# （TLS 握手成功后直接断连，冷却十几分钟），延时集群没有；采集本来就是
# 分钟级，延时数据完全够用。K 线历史走 push2his。
_QUOTE_HOST = "https://push2delay.eastmoney.com"
_HIS_HOST = "https://push2his.eastmoney.com"

# 沪深 A 股（含创业板/科创板），与 tencent/sina 的 hs_a 口径一致
_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
_PAGE = 200

_INDEX_SECIDS = {
    "000001": ("1.000001", "上证指数"),
    "399001": ("0.399001", "深证成指"),
    "399006": ("0.399006", "创业板指"),
    "000688": ("1.000688", "科创50"),
    "000300": ("1.000300", "沪深300"),
}

_KLINE_KLT = {"1d": 101, "1w": 102, "1M": 103}


def _secid(code: str) -> str:
    """Bare code → EM secid（市场前缀 1=沪 0=深）。"""
    return f"1.{code}" if code.startswith(("6", "5", "9")) else f"0.{code}"


def _f(value, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (ValueError, TypeError):
        return default
    return v


class EastmoneyProvider:
    """A 股行情 via 东方财富公开接口（直连，不依赖 akshare 包）。

    定位是 akshare/tencent 之后的兜底：quotes/indices 用延时集群一次翻页
    拉全市场（自带 PE/PB/ROE/市值），K 线用 push2his（批量回补时可能触发
    限流，由上层熔断/退避）。
    """

    name = "eastmoney"

    def __init__(self) -> None:
        self._client = httpx.Client(
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
            trust_env=False,
        )
        self._universe_cache: list[StockMeta] | None = None

    def _get_json(self, url: str, params: dict) -> dict:
        last: Exception | None = None
        for attempt in range(3):
            try:
                r = self._client.get(url, params=params)
                r.raise_for_status()
                return r.json()
            except (httpx.HTTPStatusError, httpx.TransportError, ValueError) as exc:
                last = exc
                time.sleep(1 + attempt * 2)
        raise last  # type: ignore[misc]

    def _clist(self, fields: str) -> list[dict]:
        """全市场快照翻页拉取 → diff 行列表。"""
        out: list[dict] = []
        page = 1
        while True:
            data = self._get_json(
                f"{_QUOTE_HOST}/api/qt/clist/get",
                params={
                    "pn": page, "pz": _PAGE, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                    "fid": "f12", "fs": _FS, "fields": fields,
                },
            )
            block = (data.get("data") or {})
            rows = block.get("diff") or []
            out.extend(rows)
            total = int(block.get("total") or 0)
            if not rows or len(out) >= total:
                break
            page += 1
            time.sleep(0.2)  # 温和翻页，全市场 ~28 页
        return out

    def get_universe(self, refresh: bool = False) -> list[StockMeta]:
        if self._universe_cache and not refresh:
            return self._universe_cache
        out: list[StockMeta] = []
        # f100 是东财行业板块名——与腾讯的申万二级不同源但自成体系，
        # 作为兜底 provider 时行业筛选仍然可用（行业列表接口是动态取值的）
        for row in self._clist("f12,f13,f14,f100"):
            code = str(row.get("f12") or "")
            if not code:
                continue
            industry = str(row.get("f100") or "—")
            out.append(
                StockMeta(
                    code=code,
                    name=str(row.get("f14") or code),
                    market="SH" if row.get("f13") == 1 else "SZ",
                    industry="—" if industry in ("-", "") else industry,
                )
            )
        self._universe_cache = out
        return out

    def get_quotes(self, codes: list[str] | None = None) -> list[QuoteData]:
        now = datetime.now(timezone.utc)
        rows = self._clist(
            "f2,f3,f5,f6,f8,f9,f12,f13,f14,f15,f16,f17,f18,f20,f23,f37,f124"
        )
        wanted = set(codes) if codes else None
        out: list[QuoteData] = []
        for r in rows:
            code = str(r.get("f12") or "")
            if not code or (wanted is not None and code not in wanted):
                continue
            price = _f(r.get("f2"))
            if price <= 0:  # 停牌/未成交行给 "-"
                continue
            # f124 为行情 unix 时间戳（秒），转交易所时间（CST naive），
            # 节假日返回旧数据时靠它避免误写当日 K
            exchange_ts = None
            ts_raw = r.get("f124")
            if isinstance(ts_raw, (int, float)) and ts_raw > 0:
                exchange_ts = datetime.fromtimestamp(ts_raw, tz=timezone.utc).replace(
                    tzinfo=None
                ) + timedelta(hours=8)
            out.append(
                QuoteData(
                    code=code,
                    price=price,
                    prev_close=_f(r.get("f18"), price),
                    open=_f(r.get("f17"), price),
                    high=_f(r.get("f15"), price),
                    low=_f(r.get("f16"), price),
                    volume=int(_f(r.get("f5"))),                      # 手
                    turnover=round(_f(r.get("f6")) / 1e8, 2),          # 元 → 亿
                    turnover_rate=_f(r.get("f8")),
                    pe=_f(r.get("f9")) or None,
                    pb=_f(r.get("f23")),
                    market_cap=round(_f(r.get("f20")) / 1e8, 2),       # 元 → 亿
                    roe=_f(r.get("f37")),                              # 加权 ROE(%)
                    ts=now,
                    exchange_ts=exchange_ts,
                )
            )
        return out

    def get_kline(self, code: str, period: str = "1d", limit: int = 250) -> list[Candle]:
        return self._fetch_kline(_secid(code), period, limit)

    def get_index_kline(self, code: str, limit: int = 760) -> list[Candle]:
        pair = _INDEX_SECIDS.get(code)
        if pair is None:
            return []
        return self._fetch_kline(pair[0], "1d", limit)

    def _fetch_kline(self, secid: str, period: str, limit: int) -> list[Candle]:
        data = self._get_json(
            f"{_HIS_HOST}/api/qt/stock/kline/get",
            params={
                "secid": secid,
                "klt": _KLINE_KLT.get(period, 101),
                "fqt": 1,  # 前复权，与 akshare/tencent 口径一致
                "end": "20500101",
                "lmt": limit,
                "fields1": "f1,f2,f3",
                "fields2": "f51,f52,f53,f54,f55,f56",
            },
        )
        rows = (data.get("data") or {}).get("klines") or []
        out: list[Candle] = []
        for row in rows:  # "2026-07-31,open,close,high,low,volume(手)"
            parts = str(row).split(",")
            if len(parts) < 6:
                continue
            out.append(
                Candle(
                    ts=datetime.fromisoformat(parts[0]),
                    open=float(parts[1]),
                    high=float(parts[3]),
                    low=float(parts[4]),
                    close=float(parts[2]),
                    volume=int(float(parts[5])),
                )
            )
        return out

    def get_fundamentals(self, codes: list[str] | None = None) -> list[FundamentalData]:
        out: list[FundamentalData] = []
        for q in self.get_quotes(codes):
            out.append(
                FundamentalData(
                    code=q.code,
                    pe=q.pe,
                    pb=q.pb,
                    roe=q.roe,
                    market_cap=q.market_cap,
                    dividend_yield=round(q.dividend_yield / 100, 4),
                )
            )
        return out

    def get_indices(self) -> list[IndexData]:
        secids = ",".join(sec for sec, _ in _INDEX_SECIDS.values())
        data = self._get_json(
            f"{_QUOTE_HOST}/api/qt/ulist.np/get",
            params={"fltt": 2, "secids": secids, "fields": "f2,f3,f4,f12,f14"},
        )
        rows = (data.get("data") or {}).get("diff") or []
        by_code = {str(r.get("f12")): r for r in rows}
        out: list[IndexData] = []
        for code, (_sec, name) in _INDEX_SECIDS.items():
            r = by_code.get(code)
            if not r:
                continue
            price = _f(r.get("f2"))
            if price <= 0:
                continue
            out.append(
                IndexData(
                    code=code,
                    name=name,
                    price=price,
                    change=_f(r.get("f4")),
                    change_pct=_f(r.get("f3")),
                )
            )
        return out
