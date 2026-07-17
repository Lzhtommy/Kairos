from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.models.watchlist import Watchlist
from app.schemas import LoginIn, RegisterIn, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_public(u: User) -> dict:
    return {"id": u.id, "email": u.email, "nickname": u.nickname, "tier": u.tier}


@router.post("/register", response_model=TokenOut)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    exists = db.execute(select(User).where(User.email == body.email)).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "该邮箱已注册")
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        nickname=body.nickname,
        tier="专业版",
    )
    db.add(user)
    db.flush()
    db.add(Watchlist(user_id=user.id, name="默认自选", is_default=True))
    db.commit()
    db.refresh(user)
    return {"token": create_access_token(str(user.id)), "user": _user_public(user)}


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.execute(select(User).where(User.email == body.email)).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "邮箱或密码错误")
    return {"token": create_access_token(str(user.id)), "user": _user_public(user)}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return _user_public(user)
