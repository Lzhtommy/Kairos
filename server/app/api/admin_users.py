import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import hash_password, require_admin
from app.models.strategy import Strategy
from app.models.user import User

router = APIRouter(prefix="/admin/users", tags=["admin"])


class UserPatch(BaseModel):
    isAdmin: bool


def _user_public(u: User, strategy_count: int) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "nickname": u.nickname,
        "tier": u.tier,
        "isAdmin": u.is_admin,
        "strategyCount": strategy_count,
        "createdAt": u.created_at.isoformat() if u.created_at else None,
    }


def _get_other_user(db: Session, user_id: int, me: User) -> User:
    """取目标用户；不允许对自己操作，避免管理员把自己降级或删掉后锁死后台。"""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    if user.id == me.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "不能对自己执行此操作")
    return user


@router.get("")
def list_users(db: Session = Depends(get_db), _me: User = Depends(require_admin)):
    counts = dict(
        db.execute(select(Strategy.user_id, func.count()).group_by(Strategy.user_id)).all()
    )
    users = db.execute(select(User).order_by(User.id.asc())).scalars().all()
    return [_user_public(u, counts.get(u.id, 0)) for u in users]


@router.patch("/{user_id}")
def update_user(
    user_id: int,
    body: UserPatch,
    db: Session = Depends(get_db),
    me: User = Depends(require_admin),
):
    user = _get_other_user(db, user_id, me)
    user.is_admin = body.isAdmin
    db.commit()
    count = db.scalar(select(func.count()).where(Strategy.user_id == user.id)) or 0
    return _user_public(user, count)


@router.post("/{user_id}/reset-password")
def reset_password(
    user_id: int, db: Session = Depends(get_db), me: User = Depends(require_admin)
):
    user = _get_other_user(db, user_id, me)
    password = secrets.token_urlsafe(9)
    user.password_hash = hash_password(password)
    db.commit()
    return {"password": password}


@router.delete("/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db), me: User = Depends(require_admin)):
    # 策略/模拟盘/设置等靠外键 ON DELETE CASCADE 一并删除，用过的邀请码 SET NULL
    user = _get_other_user(db, user_id, me)
    db.delete(user)
    db.commit()
    return {"ok": True}
