"""退市股日 K 回补：用 BaoStock 前复权全历史整段替换（腾讯拿不到已退市股票）。

退市后不再除权，前复权序列定型，跑一次即可。BaoStock 比库里根数多才替换，
可重复执行（断点续跑）。北交所 BaoStock 没有，跳过。

用法（在 server/ 下）：
    .venv/bin/python -m scripts.backfill_delisted
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from sqlalchemy import delete, func, insert, select

from app.core.db import SessionLocal
from app.models.market import Kline, StockInfo
from app.providers import baostock_source

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("backfill_delisted")


def bars(raw: list[dict[str, str]]) -> list[dict]:
    """BaoStock 行 → kline 行：只要交易日，成交量股 → 手（与腾讯口径一致）。"""
    out = []
    for r in raw:
        if r.get("tradestatus") != "1" or not r.get("close"):
            continue
        out.append({
            "period": "1d", "ts": datetime.fromisoformat(r["date"]),
            "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]),
            "close": float(r["close"]), "volume": int(float(r["volume"] or 0)) // 100,
        })
    return out


def main() -> int:
    db = SessionLocal()
    try:
        codes = list(db.execute(
            select(StockInfo.code, StockInfo.market)
            .where(StockInfo.delist_date.is_not(None), StockInfo.market.in_(("SH", "SZ")))
            .order_by(StockInfo.code)
        ).tuples())
        have = dict(db.execute(
            select(Kline.code, func.count()).where(Kline.period == "1d").group_by(Kline.code)
        ).all())
        replaced = skipped = failed = 0
        with baostock_source.session() as bs:
            for i, (code, market) in enumerate(codes, 1):
                symbol = f"{market.lower()}.{code}"
                try:
                    try:
                        rows = bars(baostock_source.fetch_daily_qfq(bs, symbol))
                    except RuntimeError as exc:
                        # BaoStock 匿名会话服务端共享：别的进程 logout 后这里也会掉线，重登一次
                        if "未登录" not in str(exc):
                            raise
                        bs.login()
                        rows = bars(baostock_source.fetch_daily_qfq(bs, symbol))
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    logger.warning("%s 拉取失败：%s", code, exc)
                    continue
                if len(rows) <= have.get(code, 0):
                    skipped += 1
                    continue
                db.execute(delete(Kline).where(Kline.code == code, Kline.period == "1d"))
                db.execute(insert(Kline), [{"code": code, **r} for r in rows])
                db.commit()
                replaced += 1
                if i % 20 == 0:
                    logger.info("进度 %d/%d：替换 %d，跳过 %d，失败 %d",
                                i, len(codes), replaced, skipped, failed)
                time.sleep(0.05)
        logger.info("完成：替换 %d，跳过 %d，失败 %d（共 %d 只）", replaced, skipped, failed, len(codes))
        return 0 if failed < max(len(codes) // 20, 5) else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
