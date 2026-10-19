"""SQLite 每日备份。

用 sqlite3 在线备份 API 拍一致性快照（WAL 下不阻塞写入），gzip 压缩后
落到 backup_dir，只保留最近 backup_keep 份。非 SQLite 数据库直接跳过。

手动执行（在 server/ 下）：
    .venv/bin/python -m app.jobs.backup
"""

from __future__ import annotations

import gzip
import logging
import os
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import settings
from app.core.db import engine

logger = logging.getLogger("kairos.backup")

_PREFIX = "kairos-"
_SUFFIX = ".db.gz"


def backup_database(
    db_path: str | None = None,
    backup_dir: str | None = None,
    keep: int | None = None,
) -> Path | None:
    if db_path is None:
        if engine.url.get_backend_name() != "sqlite":
            return None
        db_path = engine.url.database
    if not db_path or db_path == ":memory:":
        return None
    out_dir = Path(backup_dir or settings.backup_dir)
    keep = settings.backup_keep if keep is None else keep
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y%m%d-%H%M%S")
    raw = out_dir / f"{_PREFIX}{stamp}.db.tmp"
    gz_tmp = out_dir / f"{_PREFIX}{stamp}{_SUFFIX}.tmp"
    final = out_dir / f"{_PREFIX}{stamp}{_SUFFIX}"
    try:
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(raw)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        with open(raw, "rb") as f_in, gzip.open(gz_tmp, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out, length=1 << 20)
        os.replace(gz_tmp, final)
    except Exception as exc:  # noqa: BLE001 — keep the scheduler alive
        logger.warning("Database backup failed: %s", exc)
        gz_tmp.unlink(missing_ok=True)
        return None
    finally:
        raw.unlink(missing_ok=True)

    backups = sorted(out_dir.glob(f"{_PREFIX}*{_SUFFIX}"))
    for old in backups[: max(len(backups) - keep, 0)]:
        old.unlink(missing_ok=True)
    logger.info("Database backed up to %s (%.1f MB)", final, final.stat().st_size / 1048576)
    return final


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    backup_database()
