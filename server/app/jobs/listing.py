"""股票池补上市 / 退市日期，并把已退市的 A 股写进 stock_info（BaoStock，日更）。

在市股票只补日期、不动名称和行业（那些由 refresh_reference 维护）；退市股票整行写入，
行业留 "—"。北交所 BaoStock 没有，只按代码段纠正交易所标记。
拉取失败或返回为空时整轮不写。

手动执行（在 server/ 下）：
    .venv/bin/python -m app.jobs.listing
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import func, select

from app.core.db import SessionLocal, upsert_rows
from app.models.market import StockInfo
from app.providers import baostock_source

logger = logging.getLogger("kairos.listing")


def _market(code: str, market: str) -> str:
    # 北交所代码段 4xxxxx / 8xxxxx / 92xxxx；腾讯源的股票池里有一部分带 sz 前缀
    return "BJ" if code.startswith(("4", "8", "92")) else market


def _date(s: str) -> date | None:
    return date.fromisoformat(s) if s else None


def sync(db, raw: list[dict[str, str]]) -> int:
    """raw 为 BaoStock query_stock_basic 的行；返回写入行数。"""
    # 只要 A 股（type=1），B 股 200xxx / 900xxx 不进股票池
    basics = {}
    for r in raw:
        exchange, code = r["code"].split(".", 1)
        if r["type"] == "1" and not code.startswith(("2", "9")):
            basics[code] = (exchange.upper(), r)
    if not basics:
        logger.warning("listing: baostock returned nothing, skip")
        return 0

    rows = []
    known = set()
    for s in db.execute(select(StockInfo)).scalars():
        known.add(s.code)
        ex, r = basics.get(s.code, (None, None))
        rows.append({
            "code": s.code, "name": s.name, "market": _market(s.code, s.market),
            "industry": s.industry,
            "list_date": _date(r["ipoDate"]) if r else s.list_date,
            "delist_date": _date(r["outDate"]) if r else s.delist_date,
        })
    delisted = 0
    for code, (ex, r) in basics.items():
        if code in known or r["status"] == "1":
            continue  # 在市但不在股票池里的（极新股）等 refresh_reference 收录
        rows.append({
            "code": code, "name": r["code_name"].strip(), "market": ex, "industry": "—",
            "list_date": _date(r["ipoDate"]), "delist_date": _date(r["outDate"]),
        })
        delisted += 1
    upsert_rows(db, StockInfo, rows)
    db.commit()
    logger.info("listing: %d rows, %d delisted added", len(rows), delisted)
    return len(rows)


def refresh_listing() -> int:
    db = SessionLocal()
    try:
        return sync(db, baostock_source.fetch_security_basics())
    except Exception:  # noqa: BLE001 — 数据源失败不影响其他任务，次日重试
        logger.exception("listing refresh failed")
        db.rollback()
        return 0
    finally:
        db.close()


def listing_missing() -> bool:
    db = SessionLocal()
    try:
        return not db.execute(
            select(func.count()).select_from(StockInfo).where(StockInfo.list_date.is_not(None))
        ).scalar()
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    refresh_listing()
