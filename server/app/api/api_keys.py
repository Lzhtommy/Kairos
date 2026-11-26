"""MCP 数据服务的 API Key：用户自助创建 / 吊销，管理员查看全部并调整每日上限。"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.security import get_current_user, require_admin
from app.models.api_key import ApiKey
from app.models.user import User
from app.services import api_keys

router = APIRouter(prefix="/me/api-keys", tags=["api-keys"])
admin_router = APIRouter(prefix="/admin/api-keys", tags=["admin"])


class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class LimitIn(BaseModel):
    dailyLimit: int = Field(ge=0, le=1_000_000)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _public(k: ApiKey, used: int) -> dict:
    return {
        "id": k.id, "name": k.name, "prefix": k.prefix, "dailyLimit": k.daily_limit, "usedToday": used,
        "createdAt": _iso(k.created_at), "lastUsedAt": _iso(k.last_used_at), "revokedAt": _iso(k.revoked_at),
    }


@router.get("")
def list_keys(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    keys = db.execute(
        select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.revoked_at.is_not(None), ApiKey.id.desc())
    ).scalars().all()
    used = api_keys.usage_today(db, [k.id for k in keys])
    return {"keys": [_public(k, used.get(k.id, 0)) for k in keys], "maxKeys": settings.mcp_max_keys,
            "perMinute": settings.mcp_per_minute, "defaultDailyLimit": settings.mcp_daily_limit}


@router.post("")
def create_key(body: KeyIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        key, plain = api_keys.create_key(db, user.id, body.name.strip())
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {**_public(key, 0), "key": plain}


def _revoke(db: Session, key: ApiKey | None) -> dict:
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API Key 不存在")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(timezone.utc)
        db.commit()
    return {"ok": True}


@router.delete("/{key_id}")
def revoke_key(key_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    key = db.get(ApiKey, key_id)
    return _revoke(db, key if key is not None and key.user_id == user.id else None)


@admin_router.get("")
def admin_list(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.execute(
        select(ApiKey, User.username).join(User, User.id == ApiKey.user_id)
        .order_by(ApiKey.revoked_at.is_not(None), ApiKey.id.desc())
    ).all()
    used = api_keys.usage_today(db, [k.id for k, _ in rows])
    return [{**_public(k, used.get(k.id, 0)), "username": name} for k, name in rows]


@admin_router.patch("/{key_id}")
def admin_set_limit(key_id: int, body: LimitIn, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    key = db.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API Key 不存在")
    key.daily_limit = body.dailyLimit
    db.commit()
    return _public(key, api_keys.usage_today(db, [key.id]).get(key.id, 0))


@admin_router.delete("/{key_id}")
def admin_revoke(key_id: int, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _revoke(db, db.get(ApiKey, key_id))
