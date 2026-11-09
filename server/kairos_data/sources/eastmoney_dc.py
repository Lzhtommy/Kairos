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
_PAGE = 500


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
    """拉取一个数据中心报表的全部页。查无数据时返回空列表（接口这时 result 为 null）。"""
    out: list[dict] = []
    page = 1
    while True:
        params = {"reportName": name, "columns": columns, "pageSize": _PAGE, "pageNumber": page}
        if filter:
            params["filter"] = filter
        if sort:
            params.update(sortColumns=sort, sortTypes="-1" if desc else "1")
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
