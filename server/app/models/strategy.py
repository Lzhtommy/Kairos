from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    dsl: Mapped[dict] = mapped_column(JSON, default=dict)
    code: Mapped[str] = mapped_column(Text, default="")
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user / assistant
    text: Mapped[str] = mapped_column(Text)
    code: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class StrategyRun(Base):
    """盘后自动运行的每日命中存档：diff 出新进/调出，驱动通知与卡片展示。"""

    __tablename__ = "strategy_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )
    run_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)  # 交易日 00:00
    hit_codes: Mapped[list] = mapped_column(JSON, default=list)      # 全量命中代码（diff 用）
    added: Mapped[list] = mapped_column(JSON, default=list)          # [{code,name}]，截前 50
    removed: Mapped[list] = mapped_column(JSON, default=list)
    added_count: Mapped[int] = mapped_column(Integer, default=0)     # 计数保留全量
    removed_count: Mapped[int] = mapped_column(Integer, default=0)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    # 信号前瞻收益（成熟后由盘后任务补记）：{"d1": {"n","avg","win"}, "d5":…, "d10":…}
    # 口径与事件回测一致：信号次日开盘入场，D+N 收盘相对入场价，等权平均
    forward: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PaperAccount(Base):
    """策略自动模拟盘账户：净值口径与事件回测一致（每笔占 1/N，空仓现金持平）。"""

    __tablename__ = "paper_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), unique=True, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 成交参数（与回测 BacktestIn 同名同义）：holdDays/exitRule/stopGain/stopLoss/
    # maxConcurrent/costRate；入场固定次日开盘
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    equity: Mapped[float] = mapped_column(Float, default=1.0)
    # 一字板放弃 / 仓位满放弃的信号计数（与回测披露口径一致）
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    last_settled: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PaperPosition(Base):
    """在途仓位：pending = 信号已出等待次日开盘入场；open = 持仓中。"""

    __tablename__ = "paper_positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(8), default="pending")  # pending | open
    signal_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entry_px: Mapped[float] = mapped_column(Float, default=0.0)
    last_price: Mapped[float] = mapped_column(Float, default=0.0)  # 最近收盘（逐日结算基准）
    hold_days: Mapped[int] = mapped_column(Integer, default=0)     # 入场后经过的交易日数
    defer_days: Mapped[int] = mapped_column(Integer, default=0)    # 一字涨停入场顺延计数
    exit_pending: Mapped[bool] = mapped_column(Boolean, default=False)  # 触发退出但跌停卖不出
    exit_defer: Mapped[int] = mapped_column(Integer, default=0)


class PaperTrade(Base):
    __tablename__ = "paper_trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(64), default="")
    signal_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entry_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entry_px: Mapped[float] = mapped_column(Float)
    exit_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exit_px: Mapped[float] = mapped_column(Float)
    ret: Mapped[float] = mapped_column(Float)  # round-trip 收益（含双边费率），百分数
    reason: Mapped[str] = mapped_column(String(16))  # hold | signal | stop_gain | stop_loss
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PaperEquity(Base):
    """逐交易日净值点：(account, date) 唯一，幂等结算的锚。"""

    __tablename__ = "paper_equity"

    account_id: Mapped[int] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    equity: Mapped[float] = mapped_column(Float)
    daily_return: Mapped[float] = mapped_column(Float, default=0.0)
    positions: Mapped[int] = mapped_column(Integer, default=0)


class Backtest(Base):
    __tablename__ = "backtests"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/running/done/failed
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    curve_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
