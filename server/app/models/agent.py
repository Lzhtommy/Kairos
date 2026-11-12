"""AI 操盘手：LLM agent 自主分析、下单，按股数 / 现金真实记账（app/services/agent*.py）。

账户全站共享、管理员创建。与策略模拟盘（paper_*，按净值比例记账）是两套独立的表：
- agent_orders：agent 下的单，盘后按当日 K 线撮合（开盘价 / 限价、一字板、费率）；
- agent_sessions：每次运行（盘前 / 盘后 / 周度反思）的工具调用轨迹与结论，即决策日志；
- agent_memories：agent 自己沉淀的经验条目，下单时可引用，周度反思按实盘结果评估去留；
- agent_playbooks：交易手册的历次版本（自我迭代的痕迹），当前版本冗余在账户上。
交易日字段用 TradeDay（库里 DATE，Python 侧当天零点 naive datetime，与 kline.ts 同口径）。
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.db import Base
from kairos_data.schema import TradeDay


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AgentAccount(Base):
    __tablename__ = "agent_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    initial_cash: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    # 费率：commission / minCommission / stampDuty / transferFee / slippageBps（见 agent_broker.DEFAULT_FEES）
    fees: Mapped[dict] = mapped_column(JSON, default=dict)
    benchmark: Mapped[str] = mapped_column(String(16), default="000300")
    playbook: Mapped[str] = mapped_column(Text, default="")  # 当前交易手册（每次运行注入系统提示）
    playbook_version: Mapped[int] = mapped_column(Integer, default=0)
    last_settled: Mapped[datetime | None] = mapped_column(TradeDay, nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentPosition(Base):
    __tablename__ = "agent_positions"

    account_id: Mapped[int] = mapped_column(ForeignKey("agent_accounts.id", ondelete="CASCADE"), primary_key=True)
    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    shares: Mapped[int] = mapped_column(Integer)
    cost: Mapped[float] = mapped_column(Float)  # 持仓总成本（含买入费用，卖出按比例冲减），元
    last_price: Mapped[float] = mapped_column(Float, default=0.0)  # 最近结算收盘价
    opened_at: Mapped[datetime] = mapped_column(TradeDay)  # 建仓日（红利税按持有期计）


class AgentOrder(Base):
    """status：pending 待撮合 / filled 成交 / expired 当日未成交作废 / rejected 校验失败 / cancelled 撤单。

    created_at 早于某交易日 09:30 的 pending 单参与该日撮合，所以盘前下的单当天成交，
    盘中 / 盘后下的单等下一个交易日。"""

    __tablename__ = "agent_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("agent_accounts.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    code: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(64), default="")
    side: Mapped[str] = mapped_column(String(4))  # buy | sell
    shares: Mapped[int] = mapped_column(Integer)
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)  # 空 = 开盘市价
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    reason: Mapped[str] = mapped_column(Text, default="")  # agent 的下单理由
    memory_ids: Mapped[list] = mapped_column(JSON, default=list)  # 引用的经验条目（反思时按结果归因）
    trade_date: Mapped[datetime | None] = mapped_column(TradeDay, nullable=True)  # 撮合日
    filled_shares: Mapped[int] = mapped_column(Integer, default=0)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)  # 成交金额，元
    fee: Mapped[float] = mapped_column(Float, default=0.0)  # 佣金 + 印花税 + 过户费，元
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)  # 卖单：扣费后实现盈亏
    note: Mapped[str] = mapped_column(String(255), default="")  # 撮合 / 拒单说明
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentEquity(Base):
    __tablename__ = "agent_equity"

    account_id: Mapped[int] = mapped_column(ForeignKey("agent_accounts.id", ondelete="CASCADE"), primary_key=True)
    date: Mapped[datetime] = mapped_column(TradeDay, primary_key=True)
    cash: Mapped[float] = mapped_column(Float)
    market_value: Mapped[float] = mapped_column(Float)
    total: Mapped[float] = mapped_column(Float)
    daily_return: Mapped[float] = mapped_column(Float, default=0.0)  # %
    bench_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    fees: Mapped[float] = mapped_column(Float, default=0.0)  # 当日费用合计，元


class AgentSession(Base):
    """一次 agent 运行。kind：premarket / postclose / review / manual。"""

    __tablename__ = "agent_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("agent_accounts.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    trade_date: Mapped[datetime] = mapped_column(TradeDay, index=True)  # 运行当天（CST）
    status: Mapped[str] = mapped_column(String(12), default="running")  # running / done / failed
    summary: Mapped[str] = mapped_column(Text, default="")  # agent 的最终结论（Markdown）
    steps: Mapped[list] = mapped_column(JSON, default=list)  # [{tool, args, result}]
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentMemory(Base):
    __tablename__ = "agent_memories"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("agent_accounts.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(12), default="lesson")  # lesson 教训 / rule 规则 / watch 观察
    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    retire_reason: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentPlaybook(Base):
    __tablename__ = "agent_playbooks"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("agent_accounts.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text, default="")  # 这一版改了什么、为什么
    session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
