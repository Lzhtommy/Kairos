import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.models.user import InviteCode, User
from app.schemas import LoginIn, RegisterIn, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

_USERNAME_RE = re.compile(r"^[a-z0-9_]{3,20}$")


def _user_public(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "tier": u.tier,
        "is_admin": u.is_admin,
    }


@router.post("/register", response_model=TokenOut)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    username = body.username.strip().lower()
    if not _USERNAME_RE.fullmatch(username):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "用户名需为 3-20 位字母、数字或下划线")
    exists = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "该用户名已被占用")
    invite = db.execute(
        select(InviteCode).where(InviteCode.code == body.invite_code.strip())
    ).scalar_one_or_none()
    if invite is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "邀请码无效")
    # 看 used_at 而非 used_by：用户被删后 used_by 会被外键 SET NULL，码不能因此复活
    if invite.used_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "邀请码已被使用")
    user = User(
        username=username,
        password_hash=hash_password(body.password),
        tier="专业版",
    )
    db.add(user)
    db.flush()
    # 条件更新核销：并发用同一个码时只有一个能改到行（SQLite 不支持 SELECT FOR UPDATE）
    claimed = db.execute(
        update(InviteCode)
        .where(InviteCode.id == invite.id, InviteCode.used_at.is_(None))
        .values(used_by=user.id, used_at=datetime.now(timezone.utc))
    ).rowcount
    if claimed != 1:
        db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "邀请码已被使用")
    db.commit()
    db.refresh(user)
    return {"token": create_access_token(str(user.id)), "user": _user_public(user)}


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    name = body.username.strip().lower()
    user = db.execute(select(User).where(User.username == name)).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    return {"token": create_access_token(str(user.id)), "user": _user_public(user)}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return _user_public(user)

