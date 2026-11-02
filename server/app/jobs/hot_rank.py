"""每日落库自选股的东财人气排名。

凌晨跑：前一自然日已结束，getHisList 里那天的值定稿。覆盖所有导入过的自选股；
已落库的跳过，所以启动补跑 / 重复触发都是幂等的。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.watchlist import WatchlistImportItem
from app.services import hot_rank

logger = logging.getLogger("kairos.hot_rank")


def record_daily_hot_ranks() -> None:
    db = SessionLocal()
    try:
        yesterday = (date.fromisoformat(hot_rank.today_cst()) - timedelta(days=1)).isoformat()
        codes = list(db.execute(select(WatchlistImportItem.stock_code).distinct()).scalars())
        if not codes:
            return
        n = hot_rank.ensure_daily(db, codes, {yesterday})
        logger.info("hot rank daily %s: %d codes, %d rows stored", yesterday, len(codes), n)
    except Exception:  # noqa: BLE001 — 东财接口失败不影响其他任务，次日 / 查看时会补
        logger.exception("hot rank daily failed")
        db.rollback()
    finally:
        db.close()
