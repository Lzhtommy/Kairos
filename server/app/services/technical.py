"""K 线技术形态条件：DSL `technical` 层的解释器。

与标量因子层同样的安全模型——不 eval 任何代码，每种形态是一个固定的
解释器函数，类型走白名单，数值参数在 validate_dsl 里夹到安全范围。

形态类型（参数均可调）：
- ma_trend:    MA{window} 最近 lookback 个均线值（均线每个交易日一个点，即图上
               这条线最近 lookback 天的走势）"平滑上行"——下行天数 ≤ max_down_days
               且首尾累计涨幅 ≥ min_gain_pct(%)；需要 window+lookback-1 根日 K
- ma_distance: MA{fast} 相对 MA{base} 的偏离百分比落在 [min_pct, max_pct]
- ma_rising:   windows 里每条均线今日值都高于昨日值（同步上翘）
- ma_cross:    MA{fast} 今日刚上穿(golden)/下穿(death) MA{slow}，只认首日
- daily_change: 最近 days 个交易日每日涨跌幅都在 [min, max]（%）区间
- cum_change:  近 days 日累计涨跌幅（今收相对窗口首日开盘，窗口含今日共 days 个
               交易日：days=3 即今收对前天开盘）在 [min, max]（%）区间
- expr:        白名单 AST 公式（见 expr.py / docs/EXPR-DSL-DESIGN.md），
               基础算子自由组合，兜白名单类型表达不了的长尾指标
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.market import Kline
from app.services import expr as expr_mod


@dataclass
class Bars:
    """一只股票对齐的日 K 序列（升序）。close 必有，其余按条件所需可缺。"""

    close: list[float]
    volume: list[int] | None = None
    open: list[float] | None = None
    high: list[float] | None = None
    low: list[float] | None = None

# type -> {参数名: (默认值, 下限, 上限)}；ma_rising 的 windows、ma_cross 的
# direction 结构特殊，在 validate_dsl 里单独校验。
SPECS: dict[str, dict[str, tuple[float, float, float]]] = {
    "ma_trend": {
        "window": (60, 2, 120),
        "lookback": (60, 2, 240),  # 均线值个数（≥2 才能比较首尾）
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
    # 量能：当日成交量 ≥ 前 window 日均量 × ratio（放量）
    "vol_surge": {
        "window": (20, 2, 120),
        "ratio": (2.0, 1.1, 10.0),
    },
    # 突破：收盘创前 window 日收盘新高
    "breakout": {
        "window": (60, 5, 240),
    },
    # MACD 金叉/死叉（direction 同 ma_cross，单独校验）
    "macd_cross": {
        "fast": (12, 3, 60),
        "slow": (26, 5, 120),
        "signal": (9, 2, 60),
    },
    # RSI 落于区间（超卖 [0,30] / 超买 [70,100] 等）
    "rsi_range": {
        "window": (14, 2, 60),
        "min": (0.0, 0.0, 100.0),
        "max": (30.0, 0.0, 100.0),
    },
    # 最近 days 个交易日，每日涨跌幅都在 [min, max]（%）区间
    # （连涨: min=0；连跌: max=0；"每天涨幅>2%": min=2）
    "daily_change": {
        "days": (3, 1, 60),
        "min": (-100.0, -100.0, 100.0),
        "max": (100.0, -100.0, 100.0),
    },
    # 近 days 日累计涨跌幅（今收相对窗口首日开盘，窗口含今日共 days 个交易日，%）
    # 在 [min, max] 区间
    "cum_change": {
        "days": (5, 1, 240),
        "min": (0.0, -1000.0, 1000.0),
        "max": (1000.0, -1000.0, 1000.0),
    },
}

INT_PARAMS = {"window", "lookback", "max_down_days", "fast", "base", "slow", "signal", "days"}

_MAX_BARS = 250  # 库里每只股票的日 K 上限


def _ema_full(values: list[float], n: int) -> list[float]:
    """全长 EMA（首值播种），alpha = 2/(n+1)。"""
    out: list[float] = []
    alpha = 2 / (n + 1)
    ema = values[0] if values else 0.0
    for v in values:
        ema = alpha * v + (1 - alpha) * ema
        out.append(ema)
    return out


def _rsi_full(closes: list[float], n: int) -> list[float | None]:
    """Wilder RSI，前 n 位为 None。"""
    m = len(closes)
    out: list[float | None] = [None] * m
    if m <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_g, avg_l = gains / n, losses / n
    out[n] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    for i in range(n + 1, m):
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (n - 1) + max(d, 0)) / n
        avg_l = (avg_l * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return out


def _rolling_max_prev(values: list[float], window: int) -> list[float | None]:
    """rolling_max_prev[i] = max(values[i-window .. i-1])，不足 window 时 None。单调队列 O(n)。"""
    from collections import deque

    n = len(values)
    out: list[float | None] = [None] * n
    dq: deque[int] = deque()  # 存下标，值单调递减
    for i in range(n):
        # 窗口是 [i-window, i-1]：先给 out 取值，再把 i 推进队列
        while dq and dq[0] < i - window:
            dq.popleft()
        if i >= window:
            out[i] = values[dq[0]]
        while dq and values[dq[-1]] <= values[i]:
            dq.pop()
        dq.append(i)
    return out


def passes(technical: list[dict[str, Any]], bars: Bars) -> bool:
    """point-in-time 判定 = 滚动信号序列的最后一位（单一实现杜绝语义漂移）。"""
    if not bars.close:
        return False
    return signal_series(technical, bars)[-1]


# ---- 滚动信号序列（事件驱动回测用） --------------------------------------------------
#
# signal_series(tech, bars)[t] 与 passes(tech, bars[:t+1]) 语义完全一致，
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


def signal_series(technical: list[dict[str, Any]], bars: Bars) -> list[bool]:
    closes, volumes, opens = bars.close, bars.volume, bars.open
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
            w, lb = t["window"], t["lookback"]  # lookback = 检查最近多少个均线值
            m = ma(w)
            # downs[i] = m[i] < m[i-1]（两值都存在才算）；前缀和 O(1) 查任意区间回调数
            downs = [0] * n
            for i in range(1, n):
                if m[i] is not None and m[i - 1] is not None and m[i] < m[i - 1]:
                    downs[i] = 1
            pref = [0] * (n + 1)
            for i in range(n):
                pref[i + 1] = pref[i] + downs[i]
            span = lb - 1  # 首尾均线值的下标距离 = 比较次数
            for i in range(n):
                # 取 m[i-lb+1..i] 共 lb 个均线值，首个值也须已能算出（下标 ≥ w-1）
                start = i - span
                if start < w - 1 or m[i] is None or not m[start]:
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
        elif typ == "vol_surge":
            w, ratio = t["window"], t["ratio"]
            if volumes is None or len(volumes) != n:
                ok = [False] * n
                continue
            for i in range(n):
                if i < w:
                    ok[i] = False
                else:
                    avg = sum(volumes[i - w : i]) / w  # 前 w 日均量（不含当日）
                    if not (avg > 0 and volumes[i] >= avg * ratio):
                        ok[i] = False
        elif typ == "breakout":
            prev_max = _rolling_max_prev(closes, t["window"])
            for i in range(n):
                if not ok[i]:
                    continue
                if prev_max[i] is None or closes[i] <= prev_max[i]:
                    ok[i] = False
        elif typ == "macd_cross":
            fast_e = _ema_full(closes, t["fast"])
            slow_e = _ema_full(closes, t["slow"])
            dif = [f - s for f, s in zip(fast_e, slow_e)]
            dea = _ema_full(dif, t["signal"])
            warmup = t["slow"] + t["signal"]
            death = t["direction"] == "death"
            for i in range(n):
                if not ok[i]:
                    continue
                if i < warmup:
                    ok[i] = False
                    continue
                crossed = (
                    (dif[i] < dea[i] and dif[i - 1] >= dea[i - 1])
                    if death
                    else (dif[i] > dea[i] and dif[i - 1] <= dea[i - 1])
                )
                if not crossed:
                    ok[i] = False
        elif typ == "rsi_range":
            rsi = _rsi_full(closes, t["window"])
            for i in range(n):
                if not ok[i]:
                    continue
                v = rsi[i]
                if v is None or not (t["min"] <= v <= t["max"]):
                    ok[i] = False
        elif typ == "daily_change":
            d = t["days"]
            # in_range[i] = 第 i 日涨跌幅落于 [min, max]；前缀和 O(1) 查任意窗口
            inr = [0] * n
            for i in range(1, n):
                if closes[i - 1]:
                    chg = (closes[i] / closes[i - 1] - 1) * 100
                    inr[i] = 1 if t["min"] <= chg <= t["max"] else 0
            pref = [0] * (n + 1)
            for i in range(n):
                pref[i + 1] = pref[i] + inr[i]
            for i in range(n):
                if not ok[i]:
                    continue
                # 最近 d 日 = 第 i-d+1..i 日，首日还需要 closes[i-d] 作前收
                if i < d or pref[i + 1] - pref[i - d + 1] < d:
                    ok[i] = False
        elif typ == "cum_change":
            d = t["days"]
            for i in range(n):
                if not ok[i]:
                    continue
                # 窗口含今日共 d 个交易日，首日 = i-d+1，基准取其开盘价
                base = opens[i - d + 1] if opens and i - d + 1 >= 0 else None
                if not base:
                    ok[i] = False
                    continue
                chg = (closes[i] / base - 1) * 100
                if not (t["min"] <= chg <= t["max"]):
                    ok[i] = False
        elif typ == "expr":
            sig = expr_mod.evaluate(
                t["formula"],
                {
                    "open": bars.open, "high": bars.high, "low": bars.low,
                    "close": bars.close, "volume": bars.volume,
                },
            )
            for i in range(n):
                if ok[i] and not sig[i]:
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
            need = max(need, t["window"] + t["lookback"] - 1)
        elif t["type"] == "ma_distance":
            need = max(need, t["base"])
        elif t["type"] == "ma_rising":
            need = max(need, max(t["windows"]) + 1)
        elif t["type"] == "ma_cross":
            need = max(need, t["slow"] + 1)
        elif t["type"] in ("vol_surge", "breakout"):
            need = max(need, t["window"] + 1)
        elif t["type"] == "macd_cross":
            need = max(need, t["slow"] + t["signal"] + 10)  # EMA 预热
        elif t["type"] == "rsi_range":
            need = max(need, t["window"] * 3)  # Wilder 平滑预热
        elif t["type"] == "daily_change":
            need = max(need, t["days"] + 1)
        elif t["type"] == "cum_change":
            need = max(need, t["days"])  # 窗口含今日，只需 days 根
        elif t["type"] == "expr":
            need = max(need, expr_mod.bars_needed(t["formula"]))
    return min(need, _MAX_BARS)


def series_by_code(db: Session, codes: list[str], bars: int) -> dict[str, Bars]:
    """每只股票最近 `bars` 根日 K 的 OHLCV（升序），单条窗口函数查询。"""
    if not codes:
        return {}
    rn = func.row_number().over(partition_by=Kline.code, order_by=Kline.ts.desc()).label("rn")
    sub = (
        select(
            Kline.code, Kline.close, Kline.volume, Kline.open, Kline.high, Kline.low,
            Kline.ts, rn,
        )
        .where(Kline.period == "1d", Kline.code.in_(codes))
        .subquery()
    )
    stmt = (
        select(sub.c.code, sub.c.close, sub.c.volume, sub.c.open, sub.c.high, sub.c.low)
        .where(sub.c.rn <= bars)
        .order_by(sub.c.code, sub.c.ts.asc())
    )
    out: dict[str, Bars] = {}
    for code, close, volume, open_, high, low in db.execute(stmt):
        b = out.setdefault(code, Bars(close=[], volume=[], open=[], high=[], low=[]))
        b.close.append(close)
        b.volume.append(volume or 0)
        b.open.append(open_ or 0.0)
        b.high.append(high or 0.0)
        b.low.append(low or 0.0)
    return out
