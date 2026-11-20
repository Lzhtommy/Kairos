"""全球快讯轮询任务：全天候每 15 秒一轮（海外事件不分 A 股交易时段）。"""

from __future__ import annotations

import logging

from app.core.db import SessionLocal
from app.services.news import collector

logger = logging.getLogger("kairos.news")


def collect_news() -> None:
    db = SessionLocal()
    try:
        n = collector.run(db)
        if n:
            logger.debug("news: %d new items", n)
    except Exception:  # noqa: BLE001 — 下一轮会从库重建状态再试
        logger.exception("news collect failed")
    finally:
        db.close()


def classify_news() -> None:
    from app.services.news_classify import classify_pending  # noqa: PLC0415

    db = SessionLocal()
    try:
        classify_pending(db)
    except Exception:  # noqa: BLE001
        logger.exception("news classify failed")
        db.rollback()
    finally:
        db.close()
