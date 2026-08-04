"""日 K 深度回补：把每只股票的日 K 刷到约 5 年（默认 1250 根，qfq 口径全段统一）。

用法（在 server/ 下）：
    .venv/bin/python -m scripts.backfill_kline --bars 1250 --only-missing
    .venv/bin/python -m scripts.backfill_kline --bars 1250   # 全量重写（qfq 重刷）

- 整段重写（删旧插新）：顺带修平"历史 qfq + 逐日实时 raw bar"混存造成的复权台阶
- --only-missing 跳过根数已达标的股票（断点续跑；上市不满 N 年的新股会被重复
  处理，量小无害）
- 逐股提交，内存 O(单股)；ifzq 限流时指数退避重试，不回落 Sina（未复权）
- 预计 5500 股 × 2 页请求 ≈ 1.1 万次，限速下 1.5~3 小时
"""

from __future__ import annotations

import argparse
import logging
import time

from sqlalchemy import delete, func, select

from app.core.db import SessionLocal
from app.models.market import Kline, StockInfo
from app.providers.factory import get_provider

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("backfill_kline")

_THROTTLE = 0.15   # 每股请求间隔（秒），压住 ifzq 限流
_RETRY_MAX = 4     # 单股失败重试次数（指数退避）


def backfill_code(db, provider, code: str, bars: int) -> int:
    """单股整段重写；返回写入的 bar 数（0 = 无数据）。"""
    candles = provider.get_kline_history(code, bars)
    if not candles:
        return 0
    db.execute(delete(Kline).where(Kline.code == code, Kline.period == "1d"))
    db.add_all(
        Kline(
            code=code, period="1d", ts=c.ts,
            open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume,
        )
        for c in candles
    )
    db.commit()
    return len(candles)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=1250, help="目标日 K 深度（约 5 年 = 1250）")
    ap.add_argument("--only-missing", action="store_true",
                    help="跳过根数已达标的股票（断点续跑）")
    args = ap.parse_args()

    provider = get_provider()
    if not hasattr(provider, "get_kline_history"):
        logger.error("provider %s 不支持深历史取数", provider.name)
        return 2

    db = SessionLocal()
    try:
        codes = [
            r[0] for r in db.execute(select(StockInfo.code).order_by(StockInfo.code))
        ]
        counts = {
            code: n
            for code, n in db.execute(
                select(Kline.code, func.count())
                .where(Kline.period == "1d")
                .group_by(Kline.code)
            )
        }
        done = skipped = failed = 0
        for i, code in enumerate(codes, 1):
            if args.only_missing and counts.get(code, 0) >= args.bars - 10:
                skipped += 1
                continue
            wrote = 0
            for attempt in range(_RETRY_MAX):
                try:
                    wrote = backfill_code(db, provider, code, args.bars)
                    break
                except Exception as exc:  # noqa: BLE001 — 限流/网络抖动退避后重试
                    db.rollback()
                    wait = 2 ** (attempt + 1)
                    logger.warning("%s 第 %d 次失败（%s），%ds 后重试",
                                   code, attempt + 1, exc, wait)
                    time.sleep(wait)
            else:
                failed += 1
                logger.error("%s 放弃（连续 %d 次失败）", code, _RETRY_MAX)
            if wrote:
                done += 1
            time.sleep(_THROTTLE)
            if i % 200 == 0:
                logger.info("进度 %d/%d：重写 %d，跳过 %d，失败 %d",
                            i, len(codes), done, skipped, failed)
        logger.info("完成：重写 %d，跳过 %d，失败 %d（目标 %d 根/股）",
                    done, skipped, failed, args.bars)
        return 0 if failed < max(len(codes) // 20, 5) else 1  # >5% 失败视为整体失败
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
