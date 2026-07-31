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
    if len(closes) < p["lookback"]:
        return False  # 上市时间不够长，无法检验（与 signal_series 语义一致）
    seg = closes[-p["lookback"] :]
    mas = _ma_series(seg, p["window"])
    if len(mas) < 2:
        return False
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


# ---- 滚动信号序列（事件驱动回测用） --------------------------------------------------
#
# signal_series(tech, closes)[t] 与 passes(tech, closes[:t+1]) 语义完全一致，
# 但整条时间线一次算完：先铺 MA 数组（O(n)），ma_trend 的回调计数用前缀和，
# 避免逐日重算导致的 O(n²)。


def _ma_full(closes: list[float], window: int) -> list[float | None]:
    """全长对齐的 MA 数组：前 window-1 位为 None。"""
    out: list[float | None] = [None] * len(closes)
    acc = 0.0
    for i, c in enumerate(closes):
        acc += c
        if i >= window:
            acc -= closes[i - window]
        if i >= window - 1:
            out[i] = acc / window
    return out


def signal_series(technical: list[dict[str, Any]], closes: list[float]) -> list[bool]:
    n = len(closes)
    ok = [True] * n
    ma_cache: dict[int, list[float | None]] = {}

    def ma(window: int) -> list[float | None]:
        if window not in ma_cache:
            ma_cache[window] = _ma_full(closes, window)
        return ma_cache[window]

    for t in technical:
        typ = t["type"]
        if typ == "ma_trend":
            w, lb = t["window"], t["lookback"]
            m = ma(w)
            # downs[i] = m[i] < m[i-1]（两值都存在才算）；前缀和 O(1) 查任意区间回调数
            downs = [0] * n
            for i in range(1, n):
                if m[i] is not None and m[i - 1] is not None and m[i] < m[i - 1]:
                    downs[i] = 1
            pref = [0] * (n + 1)
            for i in range(n):
                pref[i + 1] = pref[i] + downs[i]
            span = lb - w  # 窗口内 MA 值数量 - 1 = 比较次数
            for i in range(n):
                # 与 point-in-time 版本一致：取 closes[i-lb+1..i] 内的 MA 序列
                start = i - span
                if i + 1 < lb or start < w - 1 or m[start] is None or m[i] is None or not m[start]:
                    ok[i] = False
                    continue
                if pref[i + 1] - pref[start + 1] > t["max_down_days"]:
                    ok[i] = False
                    continue
                if (m[i] / m[start] - 1) * 100 < t["min_gain_pct"]:
                    ok[i] = False
        elif typ == "ma_distance":
            mf, mb = ma(t["fast"]), ma(t["base"])
            for i in range(n):
                if not ok[i]:
                    continue
                f, b = mf[i], mb[i]
                if f is None or b is None or b <= 0:
                    ok[i] = False
                    continue
                pct = (f / b - 1) * 100
                if not (t["min_pct"] <= pct <= t["max_pct"]):
                    ok[i] = False
        elif typ == "ma_rising":
            mas = [ma(w) for w in t["windows"]]
            for i in range(n):
                if not ok[i]:
                    continue
                for m in mas:
                    if i == 0 or m[i] is None or m[i - 1] is None or m[i] <= m[i - 1]:
                        ok[i] = False
                        break
        elif typ == "ma_cross":
            mf, ms = ma(t["fast"]), ma(t["slow"])
            death = t["direction"] == "death"
            for i in range(n):
                if not ok[i]:
                    continue
                if i == 0 or None in (mf[i], ms[i], mf[i - 1], ms[i - 1]):
                    ok[i] = False
                    continue
                crossed = (
                    (mf[i] < ms[i] and mf[i - 1] >= ms[i - 1])
                    if death
                    else (mf[i] > ms[i] and mf[i - 1] <= ms[i - 1])
                )
                if not crossed:
                    ok[i] = False
    return ok


def reverse_signal(technical: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """退出规则 "signal" 用的反向条件：只对含 ma_cross 的策略有意义。"""
    crosses = [t for t in technical if t["type"] == "ma_cross"]
    if not crosses:
        return None
    return [
        {**t, "direction": "golden" if t["direction"] == "death" else "death"} for t in crosses
    ]


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
