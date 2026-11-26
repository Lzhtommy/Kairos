"""MCP 数据服务的 API Key 与每日用量（app/mcp）。新表由启动时 create_all 建，不需要迁移。"""

from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ApiKey(Base):
    """明文只在创建时返回一次，库里只存 SHA-256（Key 本身 256 位随机，不需要慢哈希）。"""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    prefix: Mapped[str] = mapped_column(String(16))  # 明文前几位，列表里辨认用
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    daily_limit: Mapped[int] = mapped_column(Integer)  # 每日工具调用上限，管理员可单独调整
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class ApiKeyUsage(Base):
    """每个 Key 每个自然日（北京时间）的工具调用次数。"""

    __tablename__ = "api_key_usage"

    key_id: Mapped[int] = mapped_column(ForeignKey("api_keys.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)
