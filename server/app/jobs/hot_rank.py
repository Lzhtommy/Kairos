"""每日落库自选股的东财人气排名。

凌晨跑：前一日（工作日）已结束，getHisList 里那天的值定稿。覆盖全市场（选股结果
命中上千只时也能直接读库出「昨日人气」）；已落库的跳过，所以启动补跑 / 重复触发都是幂等的。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.market import StockInfo
from app.services import hot_rank

logger = logging.getLogger("kairos.hot_rank")

_BATCH = 500


def record_daily_hot_ranks() -> None:
    db = SessionLocal()
    try:
        prev = date.fromisoformat(hot_rank.today_cst()) - timedelta(days=1)
        # 列表只对比交易日的排名，周末的不用专门拉（getHisList 顺带落库也无妨）
        if prev.weekday() >= 5:
            return
        yesterday = prev.isoformat()
        codes = list(db.execute(select(StockInfo.code).order_by(StockInfo.code)).scalars())
        if not codes:
            return
        # 分批：首次每只会落 120 天历史，一次全市场 ~66 万行 ORM 对象撑爆小内存机器
        n = 0
        for i in range(0, len(codes), _BATCH):
            n += hot_rank.ensure_daily(db, codes[i : i + _BATCH], {yesterday})
        logger.info("hot rank daily %s: %d codes, %d rows stored", yesterday, len(codes), n)
    except Exception:  # noqa: BLE001 — 东财接口失败不影响其他任务，次日 / 查看时会补
        logger.exception("hot rank daily failed")
        db.rollback()
    finally:
        db.close()
