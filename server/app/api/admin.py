import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import require_admin
from app.models.user import InviteCode, User

router = APIRouter(prefix="/admin/invites", tags=["admin"], dependencies=[Depends(require_admin)])


class GenerateIn(BaseModel):
    count: int = Field(default=1, ge=1, le=100)


def _invite_public(c: InviteCode, users: dict[int, User]) -> dict:
    used_by = users.get(c.used_by) if c.used_by is not None else None
    return {
        "id": c.id,
        "code": c.code,
        "usedBy": used_by.email if used_by else None,
        "usedAt": c.used_at.isoformat() if c.used_at else None,
        "createdAt": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("")
def list_invites(db: Session = Depends(get_db)):
    codes = db.execute(select(InviteCode).order_by(InviteCode.id.desc())).scalars().all()
    user_ids = {c.used_by for c in codes if c.used_by is not None}
    users = (
        {u.id: u for u in db.execute(select(User).where(User.id.in_(user_ids))).scalars()}
        if user_ids
        else {}
    )
    return [_invite_public(c, users) for c in codes]


@router.post("")
def generate_invites(body: GenerateIn, db: Session = Depends(get_db)):
    codes = [InviteCode(code=secrets.token_urlsafe(9)) for _ in range(body.count)]
    db.add_all(codes)
    db.commit()
    return [_invite_public(c, {}) for c in codes]


@router.delete("/{invite_id}")
def delete_invite(invite_id: int, db: Session = Depends(get_db)):
    invite = db.get(InviteCode, invite_id)
    if invite is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "邀请码不存在")
    if invite.used_by is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "已使用的邀请码不能删除")
    db.delete(invite)
    db.commit()
    return {"ok": True}
