"""东方财富人气榜（股吧热度）排名。

- 批量榜单 getAllCurrentList 只返回前 100 名，自选股里大部分票不在其中，所以
  实时排名逐只调 getCurrentLatest（单次 ~100ms，生产 ECS 实测可达），缓存 5 分钟。
- 每日排名逐只调 getHisList（一次给最近 120 个自然日，周末也有），落库到
  hot_rank_daily；当天的值还在变，只存已结束的日子。
接口失败的股票返回 None，不拦列表展示。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import TypeVar

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.market import HotRankDaily

logger = logging.getLogger(__name__)

_HOST = "https://emappdata.eastmoney.com/stockrank"
# 东财 App 的公开参数，akshare stock_hot_rank_* 同款
_BODY = {"appId": "appId01", "globalId": "786e4c21-70dc-435a-93bb-38", "marketType": ""}
_TTL = 300
_WORKERS = 8

_cache: dict[str, tuple[float, int | None]] = {}
# 全市场实时排名快照：(CST 日期, {code: rank})，盘中定时任务整体替换（单进程部署）
_market: tuple[str, dict[str, int]] = ("", {})
# getHisList 拉过但仍缺日子的股票（新股 / 东财没数据），冷却期内不重复拉
_his_tried: dict[str, float] = {}
_lock = threading.Lock()

T = TypeVar("T")


def today_cst() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).date().isoformat()


def _src_code(code: str) -> str:
    if code.startswith(("4", "8", "92")):
        return f"BJ{code}"
    return f"SH{code}" if code.startswith(("6", "9")) else f"SZ{code}"


def _fan_out(codes: list[str], fn: Callable[[httpx.Client, str], T]) -> dict[str, T]:
    with httpx.Client(timeout=5, trust_env=False, headers={"User-Agent": "Mozilla/5.0"}) as client:
        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            return dict(zip(codes, pool.map(lambda c: fn(client, c), codes)))


def _post(client: httpx.Client, path: str, code: str):
    r = client.post(f"{_HOST}/{path}", json={**_BODY, "srcSecurityCode": _src_code(code)})
    r.raise_for_status()
    return r.json().get("data")


def _fetch_latest(client: httpx.Client, code: str) -> int | None:
    try:
        rank = (_post(client, "getCurrentLatest", code) or {}).get("rank")
        return int(rank) if rank else None
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return None


def _fetch_history(client: httpx.Client, code: str) -> dict[str, int] | None:
    try:
        rows = _post(client, "getHisList", code) or []
        return {str(r["calcTime"])[:10]: int(r["rank"]) for r in rows if r.get("rank")}
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        return None


def hot_ranks(codes: list[str]) -> dict[str, int | None]:
    """实时排名。"""
    now = time.monotonic()
    with _lock:
        fresh = {c: v[1] for c in codes if (v := _cache.get(c)) and now - v[0] < _TTL}
    todo = [c for c in codes if c not in fresh]
    if not todo:
        return fresh

    ranks = _fan_out(todo, _fetch_latest)
    failed = sum(1 for v in ranks.values() if v is None)
    if failed:
        logger.warning("hot rank: %d/%d lookups failed", failed, len(todo))
    with _lock:
        for c, v in ranks.items():
            # 失败的不缓存，下次刷新重试
            if v is not None:
                _cache[c] = (now, v)
    return {**fresh, **ranks}


def refresh_market(codes: list[str]) -> int:
    """逐只拉全市场实时排名，整体替换快照；返回拿到排名的股票数。"""
    global _market
    ranks = _fan_out(codes, _fetch_latest)
    got = {c: r for c, r in ranks.items() if r is not None}
    if len(got) < len(codes) // 2:
        # 大面积失败（限流 / 断网）不覆盖，保留上一轮
        logger.warning("hot rank market refresh: only %d/%d ok, keep previous", len(got), len(codes))
        return len(got)
    _market = (today_cst(), got)
    return len(got)


def market_ranks(codes: list[str]) -> dict[str, int]:
    """从全市场快照取当天的实时排名；快照不是今天的（还没开盘刷新过）返回空。"""
    day, ranks = _market
    if day != today_cst():
        return {}
    return {c: ranks[c] for c in codes if c in ranks}


def market_snapshot() -> dict[str, int]:
    """最近一轮全市场实时排名快照（不限当天）：盘后 / 次日开盘前选股用上个交易日收盘前的值。"""
    return _market[1]


def ensure_daily(db: Session, codes: Iterable[str], dates: Iterable[str]) -> int:
    """保证 codes 在 dates 这些（已结束的）日子都有落库排名，缺的用 getHisList 补。

    一次 getHisList 顺带把 120 天历史全部落库。返回新写入的行数。
    """
    today = today_cst()
    dates = {d for d in dates if d < today}
    codes = list(dict.fromkeys(codes))
    if not dates or not codes:
        return 0
    have = set(
        db.execute(
            select(HotRankDaily.code, HotRankDaily.date).where(
                HotRankDaily.code.in_(codes), HotRankDaily.date.in_(dates)
            )
        ).all()
    )
    now = time.monotonic()
    with _lock:
        todo = [
            c for c in codes
            if any((c, d) not in have for d in dates) and now - _his_tried.get(c, -_TTL) >= _TTL
        ]
        for c in todo:
            _his_tried[c] = now
    if not todo:
        return 0

    histories = _fan_out(todo, _fetch_history)
    failed = sum(1 for v in histories.values() if v is None)
    if failed:
        logger.warning("hot rank history: %d/%d lookups failed", failed, len(todo))
    got = {c: h for c, h in histories.items() if h}
    if not got:
        return 0
    stored = set(
        db.execute(
            select(HotRankDaily.code, HotRankDaily.date).where(HotRankDaily.code.in_(list(got)))
        ).all()
    )
    rows = [
        HotRankDaily(code=c, date=d, rank=r)
        for c, h in got.items()
        for d, r in h.items()
        if d < today and (c, d) not in stored
    ]
    db.add_all(rows)
    db.commit()
    return len(rows)


def ranks_on(db: Session, date: str) -> dict[str, int]:
    """某日落库的全部排名（回测按历史日取值，不拿今天的冒充）。"""
    return dict(
        db.execute(
            select(HotRankDaily.code, HotRankDaily.rank).where(HotRankDaily.date == date)
        ).all()
    )


def daily_ranks(db: Session, codes: list[str], date: str) -> dict[str, int]:
    if not codes:
        return {}
    return dict(
        db.execute(
            select(HotRankDaily.code, HotRankDaily.rank).where(
                HotRankDaily.code.in_(codes), HotRankDaily.date == date
            )
        ).all()
    )
