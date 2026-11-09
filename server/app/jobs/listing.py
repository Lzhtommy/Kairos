"""股票池补上市 / 退市日期，并把已退市的 A 股写进 stock_info（BaoStock，日更）。

逻辑在 kairos_data.sync.sync_listing。Kairos 的在市股票池由行情源维护
（refresh_reference），这里不新增在市股票，只补日期和退市股。

手动执行（在 server/ 下）：
    .venv/bin/python -m app.jobs.listing
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models.market import StockInfo
from kairos_data import sync

logger = logging.getLogger("kairos.listing")


def refresh_listing() -> int:
    db = SessionLocal()
    try:
        return sync.refresh_listing(db, add_listed=False)
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
