"""命令行：把 A 股公开数据抓到本地库里。

    kairos-data init                     # 建表
    kairos-data listing                  # 股票池（在市 + 已退市，含上市 / 退市日期）
    kairos-data backfill [--parts ...]   # 全量：财报、分红、宏观、指数成分
    kairos-data daily                    # 增量：最近报告期、最新宏观、当日指数成分
    kairos-data announcements            # 最新公告
    kairos-data delisted                 # 退市股前复权日 K
    kairos-data all                      # init + listing + backfill + delisted

数据库默认 ./kairos_data.db（SQLite），用 --db 或环境变量 KAIROS_DATA_DB 指定其他库，
例如 mysql+pymysql://user:pass@host/db?charset=utf8mb4。
"""

from __future__ import annotations

import argparse
import logging
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kairos_data import schema, sync

COMMANDS = ("init", "listing", "backfill", "daily", "announcements", "delisted", "all")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kairos-data", description="A 股公开数据采集入库")
    ap.add_argument("command", choices=COMMANDS)
    ap.add_argument("--db", default=os.environ.get("KAIROS_DATA_DB", "sqlite:///kairos_data.db"))
    ap.add_argument("--parts", default=",".join(sync.PARTS),
                    help=f"backfill 的范围，逗号分隔：{','.join(sync.PARTS)}")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    engine = create_engine(args.db, pool_pre_ping=True)
    schema.metadata.create_all(engine)
    if args.command == "init":
        return 0
    with Session(engine) as db:
        if args.command in ("listing", "all"):
            sync.refresh_listing(db)
        if args.command in ("backfill", "all"):
            sync.backfill(db, tuple(p for p in args.parts.split(",") if p))
        if args.command == "daily":
            sync.daily(db)
        if args.command == "announcements":
            logging.info("announcements: %d rows", sync.refresh_announcements(db))
        if args.command in ("delisted", "all"):
            _replaced, _skipped, failed = sync.backfill_delisted(db)
            return 1 if failed else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
