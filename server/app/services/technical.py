"""K 线技术形态条件：DSL `technical` 层的解释器。

与标量因子层同样的安全模型——不 eval 任何代码，每种形态是一个固定的
解释器函数，类型走白名单，数值参数在 validate_dsl 里夹到安全范围。

形态类型（参数均可调）：
- ma_trend:    MA{window} 在最近 lookback 根 K 线内"平滑上行"——
               逐日滚动计算 MA，下行天数 ≤ max_down_days 且首尾累计涨幅 ≥ min_gain_pct(%)
- ma_distance: MA{fast} 相对 MA{base} 的偏离百分比落在 [min_pct, max_pct]
- ma_rising:   windows 里每条均线今日值都高于昨日值（同步上翘）
- ma_cross:    MA{fast} 今日刚上穿(golden)/下穿(death) MA{slow}，只认首日
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.market import Kline

# type -> {参数名: (默认值, 下限, 上限)}；ma_rising 的 windows、ma_cross 的
# direction 结构特殊，在 validate_dsl 里单独校验。
SPECS: dict[str, dict[str, tuple[float, float, float]]] = {
    "ma_trend": {
        "window": (60, 2, 120),
        "lookback": (120, 10, 240),
        "max_down_days": (10, 0, 240),
        "min_gain_pct": (1.5, -100.0, 1000.0),
    },
    "ma_distance": {
        "fast": (3, 1, 120),
        "base": (60, 2, 120),
        "min_pct": (-8.0, -100.0, 100.0),
        "max_pct": (12.0, -100.0, 100.0),
    },
    "ma_rising": {},
    "ma_cross": {
        "fast": (3, 1, 120),
        "slow": (7, 2, 120),
    },
}

INT_PARAMS = {"window", "lookback", "max_down_days", "fast", "base", "slow"}

_MAX_BARS = 250  # 库里每只股票的日 K 上限


def _ma_last(closes: list[float], window: int, offset: int = 0) -> float | None:
    """以倒数第 1+offset 根 K 线收盘的 MA{window}；数据不足返回 None。"""
    end = len(closes) - offset
    if end < window:
        return None
    return sum(closes[end - window : end]) / window


def _ma_series(closes: list[float], window: int) -> list[float]:
    """整段收盘序列的滚动 MA{window}（长度 = len(closes) - window + 1）。"""
    out: list[float] = []
    acc = 0.0
    for i, c in enumerate(closes):
        acc += c
        if i >= window:
            acc -= closes[i - window]
        if i >= window - 1:
            out.append(acc / window)
    return out


def _ma_trend(p: dict[str, Any], closes: list[float]) -> bool:
    seg = closes[-p["lookback"] :]
    mas = _ma_series(seg, p["window"])
    if len(mas) < 2:
        return False  # 上市时间不够长，无法检验
    downs = sum(1 for prev, cur in zip(mas, mas[1:]) if cur < prev)
    if not mas[0]:
        return False
    gain_pct = (mas[-1] / mas[0] - 1) * 100
    return downs <= p["max_down_days"] and gain_pct >= p["min_gain_pct"]


def _ma_distance(p: dict[str, Any], closes: list[float]) -> bool:
    fast = _ma_last(closes, p["fast"])
    base = _ma_last(closes, p["base"])
    if fast is None or base is None or base <= 0:
        return False
    pct = (fast / base - 1) * 100
    return p["min_pct"] <= pct <= p["max_pct"]


def _ma_rising(p: dict[str, Any], closes: list[float]) -> bool:
    for w in p["windows"]:
        today = _ma_last(closes, w)
        prev = _ma_last(closes, w, offset=1)
        if today is None or prev is None or today <= prev:
            return False
    return True


def _ma_cross(p: dict[str, Any], closes: list[float]) -> bool:
    ft = _ma_last(closes, p["fast"])
    st = _ma_last(closes, p["slow"])
    fy = _ma_last(closes, p["fast"], offset=1)
    sy = _ma_last(closes, p["slow"], offset=1)
    if ft is None or st is None or fy is None or sy is None:
        return False
    if p["direction"] == "death":
        return ft < st and fy >= sy
    return ft > st and fy <= sy  # 昨日仍在下方/重合，今日才算"刚金叉"


CHECKS = {
    "ma_trend": _ma_trend,
    "ma_distance": _ma_distance,
    "ma_rising": _ma_rising,
    "ma_cross": _ma_cross,
}


def passes(technical: list[dict[str, Any]], closes: list[float]) -> bool:
    return all(CHECKS[t["type"]](t, closes) for t in technical)


def bars_needed(technical: list[dict[str, Any]]) -> int:
    """一组技术条件最多需要多少根日 K（用来限定取数窗口）。"""
    need = 0
    for t in technical:
        if t["type"] == "ma_trend":
            need = max(need, t["lookback"])
        elif t["type"] == "ma_distance":
            need = max(need, t["base"])
        elif t["type"] == "ma_rising":
            need = max(need, max(t["windows"]) + 1)
        elif t["type"] == "ma_cross":
            need = max(need, t["slow"] + 1)
    return min(need, _MAX_BARS)


def closes_by_code(db: Session, codes: list[str], bars: int) -> dict[str, list[float]]:
    """每只股票最近 `bars` 根日 K 收盘价（升序），单条窗口函数查询。"""
    if not codes:
        return {}
    rn = func.row_number().over(partition_by=Kline.code, order_by=Kline.ts.desc()).label("rn")
    sub = (
        select(Kline.code, Kline.close, Kline.ts, rn)
        .where(Kline.period == "1d", Kline.code.in_(codes))
        .subquery()
    )
    stmt = (
        select(sub.c.code, sub.c.close)
        .where(sub.c.rn <= bars)
        .order_by(sub.c.code, sub.c.ts.asc())
    )
    out: dict[str, list[float]] = {}
    for code, close in db.execute(stmt):
        out.setdefault(code, []).append(close)
    return out
