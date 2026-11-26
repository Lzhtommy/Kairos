"""MCP API Key：签发、校验、计量。

Key 形如 kai_<43 位 url-safe 随机串>（256 位熵），库里只存 SHA-256。校验按哈希唯一索引一次查到。
限额两层：每 Key 每日调用上限（库里计数，跨重启）+ 每分钟上限（进程内滑动窗口，单 worker 部署够用）。
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.api_key import ApiKey, ApiKeyUsage

PREFIX = "kai_"
_CST = timezone(timedelta(hours=8))


class QuotaExceeded(Exception):
    pass


def today_cst() -> date:
    return datetime.now(_CST).date()


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_key(db: Session, user_id: int, name: str) -> tuple[ApiKey, str]:
    """返回 (记录, 明文)。明文只此一次。"""
    active = db.execute(
        select(ApiKey.id).where(ApiKey.user_id == user_id, ApiKey.revoked_at.is_(None))
    ).all()
    if len(active) >= settings.mcp_max_keys:
        raise ValueError(f"每个账户最多 {settings.mcp_max_keys} 个有效 API Key，请先吊销不用的")
    plain = PREFIX + secrets.token_urlsafe(32)
    key = ApiKey(user_id=user_id, name=name, prefix=plain[:10], key_hash=hash_key(plain),
                 daily_limit=settings.mcp_daily_limit)
    db.add(key)
    db.commit()
    db.refresh(key)
    return key, plain


def lookup(db: Session, plain: str) -> ApiKey | None:
    """有效（未吊销）的 Key；格式不对直接拒，不查库。"""
    if not plain.startswith(PREFIX) or len(plain) > 128:
        return None
    key = db.execute(select(ApiKey).where(ApiKey.key_hash == hash_key(plain))).scalar_one_or_none()
    if key is None or key.revoked_at is not None:
        return None
    return key


def usage_today(db: Session, key_ids: list[int]) -> dict[int, int]:
    if not key_ids:
        return {}
    return dict(db.execute(
        select(ApiKeyUsage.key_id, ApiKeyUsage.calls)
        .where(ApiKeyUsage.key_id.in_(key_ids), ApiKeyUsage.day == today_cst())
    ).tuples().all())


_windows: dict[int, deque[float]] = {}
_lock = threading.Lock()


def _check_rate(key_id: int, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    with _lock:
        win = _windows.setdefault(key_id, deque())
        while win and now - win[0] >= 60:
            win.popleft()
        if len(win) >= settings.mcp_per_minute:
            raise QuotaExceeded(f"调用过于频繁：每分钟最多 {settings.mcp_per_minute} 次，请稍后再试")
        win.append(now)


def meter(db: Session, key_id: int) -> None:
    """记一次工具调用；超过每分钟或每日上限抛 QuotaExceeded（超限的那次不计入）。

    Key 在调用期间被吊销也在这里拦下（鉴权与调用之间可能隔着一个长连接）。
    """
    key = db.get(ApiKey, key_id)
    if key is None or key.revoked_at is not None:
        raise QuotaExceeded("API Key 已失效")
    _check_rate(key_id)
    day = today_cst()
    row = db.get(ApiKeyUsage, (key_id, day))
    used = row.calls if row else 0
    if used >= key.daily_limit:
        raise QuotaExceeded(f"今日调用已达上限 {key.daily_limit} 次，北京时间零点重置")
    key.last_used_at = datetime.now(timezone.utc)
    if row is None:
        db.add(ApiKeyUsage(key_id=key_id, day=day, calls=1))
        try:
            db.commit()
            return
        except IntegrityError:  # 并发的另一次调用先建了当天这行
            db.rollback()
            db.get(ApiKey, key_id).last_used_at = datetime.now(timezone.utc)
    # 原子自增：同一 Key 并发调用时不丢计数
    db.execute(update(ApiKeyUsage).where(ApiKeyUsage.key_id == key_id, ApiKeyUsage.day == day)
               .values(calls=ApiKeyUsage.calls + 1))
    db.commit()
