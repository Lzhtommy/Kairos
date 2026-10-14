"""个股基本面研报（docs/RESEARCH-DESIGN.md）。

研报全站共享、管理员生成；一次生成一行，历史保留用于分数走势。
facts 存完整事实表快照——报告里每个数字都能追溯到当时的原始值与数据源。
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ResearchReport(Base):
    __tablename__ = "research_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    industry: Mapped[str] = mapped_column(String(64), default="—")
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued/running/done/failed
    progress: Mapped[str] = mapped_column(String(64), default="")
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data_as_of: Mapped[str | None] = mapped_column(String(16), nullable=True)  # 最新报告期 YYYY-MM-DD
    scoring_version: Mapped[str] = mapped_column(String(16), default="v1")
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    grade: Mapped[str | None] = mapped_column(String(4), nullable=True)
    coverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    sub_scores: Mapped[dict] = mapped_column(JSON, default=dict)
    red_flags: Mapped[list] = mapped_column(JSON, default=list)
    facts: Mapped[dict] = mapped_column(JSON, default=dict)
    sections: Mapped[list] = mapped_column(JSON, default=list)
    content_md: Mapped[str] = mapped_column(Text, default="")
    sources: Mapped[list] = mapped_column(JSON, default=list)
    llm_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    llm_tokens: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResearchRawCache(Base):
    """原始数据缓存（跨报告共享）：akshare 返回的表按 records JSON 存。"""

    __tablename__ = "research_raw_cache"

    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    payload: Mapped[list] = mapped_column(JSON, default=list)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
