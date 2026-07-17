from typing import Any

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


class ChatIn(BaseModel):
    text: str = Field(min_length=1)


class BacktestIn(BaseModel):
    strategyId: int
    start: str = "2021-01-01"
    rebalance: str = "monthly_first_trading_day"
    costRate: float = 0.0005
