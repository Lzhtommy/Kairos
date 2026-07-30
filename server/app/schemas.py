from typing import Any, Literal

from pydantic import BaseModel, Field


class RegisterIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=6, max_length=128)
    nickname: str = Field(min_length=1, max_length=64)


class LoginIn(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    token: str
    user: dict[str, Any]


class UserOut(BaseModel):
    id: int
    email: str
    nickname: str
    tier: str


class ScreenerIn(BaseModel):
    industry: str | None = None
    peMin: float | None = None
    peMax: float | None = None
    roeMin: float | None = None
    page: int = 1
    pageSize: int = 100


class StrategyIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    tags: list[str] = []
    dsl: dict[str, Any] = {}
    code: str = ""


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    # 多轮对话上下文：此前的会话记录 + 右侧面板当前草稿策略（供"把 PE 收紧到 20"这类增量修改）
    history: list[ChatTurn] = []
    currentDsl: dict[str, Any] | None = None


class BacktestIn(BaseModel):
    strategyId: int
    start: str = "2021-01-01"
    rebalance: str = "monthly_first_trading_day"
    costRate: float = 0.0005
