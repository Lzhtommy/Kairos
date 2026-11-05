"""把 SQLite 库整体搬到一个空的 MySQL 库（RDS 迁移用）。

按 models 在目标库建表（Float→DOUBLE、DateTime→DATETIME(6) 由 app.core.db 统一处理），
逐表按主键顺序流式分批复制，最后逐表核对行数；alembic_version 一并带过去。
目标库里已有数据的表默认拒绝写入，--truncate 先清空再搬。

用法（在 server/ 下）：
    .venv/bin/python -m scripts.migrate_sqlite_to_mysql \\
        --source data/kairos.db \\
        --target "mysql+pymysql://user:pass@host:3306/kairos?charset=utf8mb4"
    # 或者把连接串放在环境变量 RDS_DATABASE_URL 里，省略 --target
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from sqlalchemy import Table, create_engine, func, inspect, select, text

from app.core.db import Base
import app.models  # noqa: F401  (register all models on Base.metadata)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("migrate")

_BATCH = 5000


def _copy_table(src, dst, table: Table, src_cols: set[str]) -> int:  # noqa: ANN001
    cols = [c for c in table.columns if c.name in src_cols]
    missing = [c.name for c in table.columns if c.name not in src_cols]
    if missing:
        logger.warning("%s: 源库缺列 %s，按模型默认值处理", table.name, missing)
    order = list(table.primary_key.columns) or cols[:1]
    stmt = select(*cols).order_by(*order)
    copied = 0
    t0 = time.monotonic()
    with src.connect() as s, dst.begin() as d:
        d.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        result = s.execution_options(stream_results=True).execute(stmt)
        while rows := result.fetchmany(_BATCH):
            d.execute(table.insert(), [dict(r._mapping) for r in rows])
            copied += len(rows)
            if copied % 500_000 < _BATCH:
                logger.info("  %s: %d 行（%.0f 行/秒）", table.name, copied,
                            copied / max(time.monotonic() - t0, 1e-6))
    return copied


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="data/kairos.db")
    # 默认读环境变量，免得连接串（含密码）出现在进程列表里
    ap.add_argument("--target", default=os.environ.get("RDS_DATABASE_URL"))
    ap.add_argument("--truncate", action="store_true", help="目标表非空时先清空")
    args = ap.parse_args()
    if not args.target:
        ap.error("缺少目标库：--target 或环境变量 RDS_DATABASE_URL")

    src = create_engine(f"sqlite:///{args.source}")
    dst = create_engine(args.target, pool_pre_ping=True)
    if dst.url.get_backend_name() != "mysql":
        logger.error("目标库必须是 MySQL：%s", dst.url.render_as_string(hide_password=True))
        return 2

    Base.metadata.create_all(dst)
    src_insp = inspect(src)
    src_tables = set(src_insp.get_table_names())

    with dst.connect() as d:
        nonempty = [t.name for t in Base.metadata.sorted_tables
                    if d.execute(select(func.count()).select_from(t)).scalar()]
    if nonempty:
        if not args.truncate:
            logger.error("目标库这些表已有数据：%s（确认覆盖请加 --truncate）", nonempty)
            return 2
        with dst.begin() as d:
            d.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            for name in nonempty:
                d.execute(text(f"TRUNCATE TABLE `{name}`"))

    t0 = time.monotonic()
    mismatched: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name not in src_tables:
            logger.warning("%s: 源库没有这张表，跳过", table.name)
            continue
        src_cols = {c["name"] for c in src_insp.get_columns(table.name)}
        n = _copy_table(src, dst, table, src_cols)
        with src.connect() as s, dst.connect() as d:
            want = s.execute(select(func.count()).select_from(table)).scalar()
            got = d.execute(select(func.count()).select_from(table)).scalar()
        flag = "OK" if want == got == n else "MISMATCH"
        if flag != "OK":
            mismatched.append(table.name)
        logger.info("%s: 源 %d / 目标 %d  %s", table.name, want, got, flag)

    if "alembic_version" in src_tables:
        with src.connect() as s:
            versions = [r[0] for r in s.execute(text("SELECT version_num FROM alembic_version"))]
        with dst.begin() as d:
            d.execute(text("CREATE TABLE IF NOT EXISTS alembic_version "
                           "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
            d.execute(text("DELETE FROM alembic_version"))
            for v in versions:
                d.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": v})
        logger.info("alembic_version: %s", versions)

    logger.info("完成，用时 %.0f 秒", time.monotonic() - t0)
    if mismatched:
        logger.error("行数不一致：%s", mismatched)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
