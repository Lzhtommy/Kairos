"""东财数据中心（datacenter-web）与公告中心的批量接口。

都是按报告期 / 日期整市场分页拉取，单页 0.1~0.6 秒，不走 push2 实时集群（那边有 IP 风控）。
"""

from __future__ import annotations

import time
from typing import Any

import httpx

_DC_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_ANN_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
_ANN_TEXT_URL = "https://np-cnotice-stock.eastmoney.com/api/content/ann"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
_PAGE = 2500  # 接口单页上限


def _get(url: str, params: dict[str, Any], retries: int = 3) -> dict:
    for attempt in range(retries):
        try:
            r = httpx.get(url, params=params, headers=_HEADERS, timeout=20)
            r.raise_for_status()
            return r.json()
        except (httpx.TransportError, httpx.HTTPStatusError):
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")


def fetch_report(name: str, filter: str = "", sort: str = "", desc: bool = True,
                 max_pages: int | None = None, columns: str = "ALL") -> list[dict]:
    """拉取一个数据中心报表的全部页。查无数据时返回空列表（接口这时 result 为 null）。

    sort 为逗号分隔的排序列，必须能唯一定序：不排序时翻页结果不稳定，
    实测相邻两页 500 行里 496 行重复，全量拉取会大量漏行。
    """
    out: list[dict] = []
    page = 1
    while True:
        params = {"reportName": name, "columns": columns, "pageSize": _PAGE, "pageNumber": page}
        if filter:
            params["filter"] = filter
        if sort:
            n = len(sort.split(","))
            params.update(sortColumns=sort, sortTypes=",".join(["-1" if desc else "1"] * n))
        body = _get(_DC_URL, params)
        result = body.get("result") or {}
        out.extend(result.get("data") or [])
        pages = result.get("pages") or 0
        if page >= pages or (max_pages and page >= max_pages):
            return out
        page += 1


def fetch_announcements(page: int = 1, page_size: int = 100, code: str | None = None) -> tuple[list[dict], int]:
    """公告列表（沪深京 A 股），按时间倒序。返回 (列表, 总条数)。"""
    params: dict[str, Any] = {
        "sr": -1, "page_size": page_size, "page_index": page, "ann_type": "A",
        "client_source": "web", "f_node": 0, "s_node": 0,
    }
    if code:
        params["stock_list"] = code
    data = _get(_ANN_URL, params).get("data") or {}
    return data.get("list") or [], int(data.get("total_hits") or 0)


def fetch_announcement_text(art_code: str, max_pages: int = 3) -> tuple[str, str | None]:
    """公告正文纯文本（最多前 max_pages 页）和 PDF 链接。"""
    texts: list[str] = []
    pdf = None
    for page in range(1, max_pages + 1):
        data = _get(_ANN_TEXT_URL, {"art_code": art_code, "client_source": "web",
                                    "page_index": page}).get("data") or {}
        texts.append(data.get("notice_content") or "")
        pdf = pdf or data.get("attach_url")
        if page >= int(data.get("page_size") or 1):
            break
    return "\n".join(texts).strip(), pdf


_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


def fetch_his_kline(secid: str) -> list[dict]:
    """日线全历史（不复权；push2his 延时 / 历史集群，无实时集群的 IP 风控）。

    secid 形如 1.000300（沪市 / 中证指数、沪市 ETF / 转债）、0.399001（深市、北证）、
    2.932000（中证新编指数）。返回 {date, open, close, high, low, volume, amount}，
    成交量单位手（指数、股票、ETF）或张（转债），成交额元。
    """
    body = _get(_KLINE_URL, {
        "secid": secid, "klt": 101, "fqt": 0, "lmt": 100000, "end": "20500101",
        "fields1": "f1,f2,f3", "fields2": "f51,f52,f53,f54,f55,f56,f57",
    })
    out = []
    for line in (body.get("data") or {}).get("klines") or []:
        d, o, c, h, low, vol, amt = line.split(",")[:7]
        out.append({"date": d, "open": float(o), "close": float(c), "high": float(h), "low": float(low),
                    "volume": float(vol), "amount": float(amt)})
    return out


_CLIST_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"
SNAPSHOT_FS = {
    "etf": "b:MK0021,b:MK0022,b:MK0023,b:MK0024",
    "cb": "b:MK0354",
}


def fetch_snapshot(fs: str) -> list[dict]:
    """全市场行情快照（push2delay 延时集群），收盘后即当日日线。

    返回 {code, market(1 沪 / 0 深), name, open, high, low, close, volume, amount, ts(Unix 秒)}。
    """
    out: list[dict] = []
    page = 1
    while True:
        # 接口单页最多返回 100 行（pz 再大也会截断），按累计行数翻页
        body = _get(_CLIST_URL, {"pn": page, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                                 "fid": "f12", "fs": fs,
                                 "fields": "f12,f13,f14,f2,f5,f6,f15,f16,f17,f124"})
        data = body.get("data") or {}
        diff = data.get("diff") or []
        for d in diff:
            out.append({"code": d["f12"], "market": d["f13"], "name": d["f14"], "open": d["f17"],
                        "high": d["f15"], "low": d["f16"], "close": d["f2"], "volume": d["f5"],
                        "amount": d["f6"], "ts": d["f124"]})
        if not diff or len(out) >= int(data.get("total") or 0):
            return out
        page += 1
