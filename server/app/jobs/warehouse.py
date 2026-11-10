"""数据仓库调度入口：采集与写库逻辑在 kairos_data.sync，这里只管会话和异常。

- 日更 refresh_warehouse（18:00 CST）：财报、分红、宏观、指数成分
- 公告 refresh_announcements 每小时拉最新几页；个股历史公告在查阅时按需补（app.api.data）
- 首次部署表为空时 backfill_missing 在启动后跑一轮全量（财报 1988 年起约 5 分钟）

手动执行（在 server/ 下）：
    .venv/bin/python -m app.jobs.warehouse daily
    .venv/bin/python -m app.jobs.warehouse backfill [--parts fin,div,macro,index]
    .venv/bin/python -m app.jobs.warehouse basic    # 每日指标补到上市日（数小时，可断点续跑）
"""

from __future__ import annotations

import argparse
import logging

from app.core.db import SessionLocal
from kairos_data import sync

logger = logging.getLogger("kairos.warehouse")


def refresh_warehouse() -> None:
    db = SessionLocal()
    try:
        sync.daily(db)
    finally:
        db.close()


def backfill(parts: tuple[str, ...] = sync.PARTS) -> None:
    db = SessionLocal()
    try:
        sync.backfill(db, parts)
    finally:
        db.close()


def backfill_daily_basic() -> None:
    db = SessionLocal()
    try:
        sync.backfill_daily_basic(db)
    finally:
        db.close()


def backfill_missing() -> None:
    db = SessionLocal()
    try:
        if parts := sync.missing_parts(db):
            logger.info("warehouse backfill: %s", ",".join(parts))
            sync.backfill(db, parts)
    finally:
        db.close()


def refresh_announcements() -> int:
    db = SessionLocal()
    try:
        n = sync.refresh_announcements(db)
        logger.info("announcements: %d rows", n)
        return n
    except Exception:  # noqa: BLE001
        logger.exception("announcement refresh failed")
        db.rollback()
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["daily", "backfill", "basic", "announcements"])
    ap.add_argument("--parts", default=",".join(sync.PARTS))
    args = ap.parse_args()
    if args.mode == "daily":
        refresh_warehouse()
    elif args.mode == "basic":
        backfill_daily_basic()
    elif args.mode == "announcements":
        refresh_announcements()
    else:
        backfill(tuple(args.parts.split(",")))
