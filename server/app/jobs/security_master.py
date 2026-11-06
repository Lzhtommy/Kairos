"""证券主表日更：BaoStock 全量证券（含已退市）+ stock_info 补北交所。

整表 upsert，幂等；BaoStock 拉取失败时整轮不写，不会用残缺数据覆盖。
手动执行（在 server/ 下）：
    .venv/bin/python -m app.jobs.security_master
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import func, select

from app.core.db import SessionLocal, upsert_rows
from app.models.market import SecurityMaster, StockInfo
from app.providers import baostock_source

logger = logging.getLogger("kairos.security_master")

# BaoStock type：1 股票 2 指数 3 其他 4 可转债 5 ETF
_TYPES = {"1": "stock", "2": "index", "3": "other", "4": "cbond", "5": "etf"}


def _exchange(code: str, market: str) -> str:
    # 北交所代码段 4xxxxx / 8xxxxx / 92xxxx；腾讯源的股票池里有一部分带 sz 前缀，按代码段纠正
    return "BJ" if code.startswith(("4", "8", "92")) else market


def _parse_date(s: str) -> date | None:
    return date.fromisoformat(s) if s else None


def normalize(raw: dict[str, str], now: datetime) -> dict:
    exchange, code = raw["code"].split(".", 1)
    return {
        "exchange": exchange.upper(),
        "code": code,
        "name": raw["code_name"].strip(),
        "sec_type": _TYPES.get(raw["type"], "other"),
        "list_date": _parse_date(raw["ipoDate"]),
        "delist_date": _parse_date(raw["outDate"]),
        "status": "listed" if raw["status"] == "1" else "delisted",
        "source": "baostock",
        "updated_at": now,
    }


def sync(db, raw: list[dict[str, str]]) -> int:
    """把 BaoStock 原始行写进主表，返回写入行数。raw 为空时不写。"""
    now = datetime.now(timezone.utc)
    rows = {(r["exchange"], r["code"]): r for r in (normalize(x, now) for x in raw)}
    if not rows:
        logger.warning("security master: baostock returned nothing, skip")
        return 0
    # BaoStock 没有北交所，当天新上市的也可能还没收录：用当前股票池补，日期未知留空。
    # 已在表里的不动，免得 BaoStock 偶尔漏一条时把已有的上市日期清掉。
    existing = set(db.execute(select(SecurityMaster.exchange, SecurityMaster.code)).tuples())
    added = 0
    for s in db.execute(select(StockInfo)).scalars():
        key = (_exchange(s.code, s.market), s.code)
        if key in rows or key in existing:
            continue
        rows[key] = {
            "exchange": key[0], "code": s.code, "name": s.name, "sec_type": "stock",
            "list_date": None, "delist_date": None, "status": "listed",
            "source": "kairos", "updated_at": now,
        }
        added += 1
    upsert_rows(db, SecurityMaster, list(rows.values()))
    db.commit()
    logger.info("security master: %d rows (%d from stock_info)", len(rows), added)
    return len(rows)


def refresh_security_master() -> int:
    """返回写入行数；失败返回 0（不拦调度器）。"""
    db = SessionLocal()
    try:
        return sync(db, baostock_source.fetch_security_basics())
    except Exception:  # noqa: BLE001 — 数据源失败不影响其他任务，次日重试
        logger.exception("security master refresh failed")
        db.rollback()
        return 0
    finally:
        db.close()


def security_master_empty() -> bool:
    db = SessionLocal()
    try:
        return not db.execute(select(func.count()).select_from(SecurityMaster)).scalar()
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    refresh_security_master()
