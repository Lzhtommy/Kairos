"""退市股日 K 回补：用 BaoStock 前复权全历史整段替换（逻辑在 kairos_data.sync.backfill_delisted）。

用法（在 server/ 下）：
    .venv/bin/python -m scripts.backfill_delisted
"""

from __future__ import annotations

import logging

from app.core.db import SessionLocal
from kairos_data import sync

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")


def main() -> int:
    db = SessionLocal()
    try:
        replaced, skipped, failed = sync.backfill_delisted(db)
        total = replaced + skipped + failed
        return 0 if failed < max(total // 20, 5) else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
