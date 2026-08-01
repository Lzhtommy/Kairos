from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    nickname: Mapped[str] = mapped_column(String(64))
    tier: Mapped[str] = mapped_column(String(32), default="免费版")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class UserSetting(Base):
    """用户级配置。独立成表（而非给 users 加列）：create_all 对已有表不加列，
    新表则开箱即用，免去一次线上 Alembic 迁移。"""

    __tablename__ = "user_settings"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # 策略命中变化的通知渠道：none | email | webhook
    notify_channel: Mapped[str] = mapped_column(String(16), default="none")
    notify_target: Mapped[str] = mapped_column(String(512), default="")
