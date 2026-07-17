"""Classic (non-strategy) screener — the sidebar filters on the 选股 page."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.services.market import factor_rows, stock_dicts


def run_classic(
    db: Session,
    industry: str | None = None,
    pe_min: float | None = None,
    pe_max: float | None = None,
    roe_min: float | None = None,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    rows = factor_rows(db)
    matched_codes = []
    for r in rows:
        if industry and industry != "all" and r["industry"] != industry:
            continue
        pe = r["pe"]
        if pe_min is not None or pe_max is not None:
            if pe is None:
                continue
            if pe_min is not None and pe < pe_min:
                continue
            if pe_max is not None and pe > pe_max:
                continue
        if roe_min is not None and r["roe"] < roe_min:
            continue
        matched_codes.append(r["code"])

    total = len(matched_codes)
    start = (page - 1) * page_size
    page_codes = matched_codes[start : start + page_size]
    items = stock_dicts(db, page_codes) if page_codes else []
    # preserve match order
    order = {c: i for i, c in enumerate(page_codes)}
    items.sort(key=lambda s: order.get(s["code"], 0))
    return {"total": total, "page": page, "pageSize": page_size, "items": items}
