"""行业轮动模块的数据表（research/sector_rotation Phase 1-3 的产品化落地）。

四张表全部是新表（create_all 直接建，无需 Alembic 迁移）：
- sector_info / sector_bar：板块元数据 + 日线（provider 日更）
- sector_metric：日频拥挤度快照（前向留痕，公式改动不影响历史记录）
- sector_tilt：月频倾斜建议留痕（模型当时怎么说，事后可核对）
- sector_narrative：LLM 叙事打分，**只前向记录，永不回测**
  （LLM 训练数据污染使历史回测天然虚高，见 research PLAN Step 4）
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SectorInfo(Base):
    """板块元数据。source 锁定口径（sw=申万一级 / eastmoney=东财行业板块），
    一个部署只用一种口径，混用会让截面指标失真。"""

    __tablename__ = "sector_info"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class SectorBar(Base):
    __tablename__ = "sector_bar"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(Integer, default=0)
    amount: Mapped[float] = mapped_column(Float, default=0.0)  # 成交额（亿元）


class SectorMetric(Base):
    """日频拥挤度快照（研究口径：三个子指标对自身 250 日滚动分位取均值）。"""

    __tablename__ = "sector_metric"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, index=True)
    crowd: Mapped[float | None] = mapped_column(Float, nullable=True)        # 0~1 合成
    crowd_share: Mapped[float | None] = mapped_column(Float, nullable=True)  # 成交额占比分位
    crowd_heat: Mapped[float | None] = mapped_column(Float, nullable=True)   # 换手热度分位
    crowd_bias: Mapped[float | None] = mapped_column(Float, nullable=True)   # 乖离率分位


class SectorTilt(Base):
    """月频倾斜建议留痕。month 为该月首日 00:00（建议基于该月末数据、
    作用于下一个月）。suggestion ∈ 高配/标配/低配；过热盖帽后高配降级
    会记在 capped 字段里。"""

    __tablename__ = "sector_tilt"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    month: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, index=True)
    rev1_z: Mapped[float | None] = mapped_column(Float, nullable=True)
    season_z: Mapped[float | None] = mapped_column(Float, nullable=True)
    resmom_z: Mapped[float | None] = mapped_column(Float, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    suggestion: Mapped[str] = mapped_column(String(8), default="标配")
    capped: Mapped[int] = mapped_column(Integer, default=0)  # 1 = 因过热从高配降级
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SectorNarrative(Base):
    """每日 LLM 叙事打分（DeepSeek）。只前向积累，12 个月后评估 IC 再决定
    是否进模型（PLAN Step 4 gate）；此前仅作展示层信息。"""

    __tablename__ = "sector_narrative"

    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(16), primary_key=True)  # 板块代码或 "_market"
    direction: Mapped[float] = mapped_column(Float, default=0.0)     # -1（利空）~ +1（利好）
    strength: Mapped[float] = mapped_column(Float, default=0.0)      # 0（无叙事）~ 1（沸腾）
    summary: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list] = mapped_column(JSON, default=list)       # 支撑标题原文（截前若干条）
    model: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
