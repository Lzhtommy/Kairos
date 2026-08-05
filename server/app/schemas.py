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
    # 最近一次回测的 metrics 摘要（含 worstPeriods/worstTrades），供 AI 诊断归因
    lastBacktest: dict[str, Any] | None = None


class BacktestIn(BaseModel):
    """回测参数。全部有默认值；数值范围与枚举在 backtest._clean_params 里夹紧。"""

    strategyId: int
    periodDays: int = 250          # 回测窗口（交易日）：120≈半年 250≈1年 500≈2年 750≈3年
    holdDays: int = 10             # 事件驱动：信号后持有期
    entry: str = "open"            # 事件驱动：open|close（次日）|signal_close（当日收盘）
    exitRule: str = "hold"         # 事件驱动：hold|signal|stop
    stopGain: float = 15.0         # exitRule=stop 时的止盈 %
    stopLoss: float = 8.0          # exitRule=stop 时的止损 %
    rebalance: str = "monthly"     # 组合模式：weekly|monthly|quarterly
    costRate: float = 0.0005       # 双边费率
    benchmark: str = "000300"      # 000300|000905|399006
    weighting: str = "equal"       # 组合模式：equal|cap
    maxPositions: int = 0          # 组合模式：0=不限，否则按市值取前 N
    maxConcurrent: int = 10        # 事件模式：最大同时持仓数（每笔占 1/N 仓位）


class PaperToggleIn(BaseModel):
    """模拟盘开关。params 与回测事件模式同名同义，服务端 clean_params 夹紧。"""

    enabled: bool
    params: dict[str, Any] | None = None
