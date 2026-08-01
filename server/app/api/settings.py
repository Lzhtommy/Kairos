from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import get_current_user
from app.models.user import User, UserSetting
from app.services import notify

router = APIRouter(prefix="/me/settings", tags=["settings"])

_CHANNELS = ("none", "email", "webhook")


class SettingsIn(BaseModel):
    notifyChannel: str = "none"
    notifyTarget: str = Field(default="", max_length=512)


def _get_or_create(db: Session, user_id: int) -> UserSetting:
    us = db.get(UserSetting, user_id)
    if us is None:
        us = UserSetting(user_id=user_id)
        db.add(us)
        db.commit()
        db.refresh(us)
    return us


def _public(us: UserSetting) -> dict:
    return {"notifyChannel": us.notify_channel, "notifyTarget": us.notify_target}


@router.get("")
def get_settings(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _public(_get_or_create(db, user.id))


@router.put("")
def update_settings(
    body: SettingsIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    us = _get_or_create(db, user.id)
    us.notify_channel = body.notifyChannel if body.notifyChannel in _CHANNELS else "none"
    us.notify_target = body.notifyTarget.strip()
    db.commit()
    return _public(us)


@router.post("/test")
def test_notification(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    us = _get_or_create(db, user.id)
    if us.notify_channel == "none" or not us.notify_target:
        return {"ok": False, "detail": "请先选择通知渠道并填写目标地址"}
    ok = notify.send(
        us.notify_channel,
        us.notify_target,
        "Kairos 测试通知",
        f"你好 {user.nickname}，这是一条测试消息。收到即代表策略日报通知配置成功。",
    )
    return {"ok": ok, "detail": "已发送，请查收" if ok else "发送失败，请检查地址或服务端 SMTP 配置"}
