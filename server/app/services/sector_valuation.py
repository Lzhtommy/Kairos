"""行业估值分位：纯展示层的客观数据，不参与倾斜建议。

口径：
- PE/PB/股息率来自申万宏源研究「指数分析-日报表」（整体法，非个股中位数）；
- PE ≤ 0（行业整体亏损）记缺失，不参与分位；
- 分位 = 对自身过去 WIN_DAYS 个交易日（含当日）的平均名次百分位，≥ MIN_DAYS 才出值；
- 主指标：周期 / 金融 / 地产看 PB（盈利顶点 PE 反而最低，PE 分位会说反），其余看 PE；
- 标签只描述历史位置（低位 / 中位 / 高位），不说「低估 / 高估」——结构性
  下移的行业分位会长期偏低，那是价值陷阱而非便宜。
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right, insort

from app.services.sector_metrics import _rank_pct

WIN_DAYS = 1250   # 约 5 年
MIN_DAYS = 750    # 至少 3 年历史才出分位
LOW, HIGH = 0.2, 0.8

# 周期 / 金融 / 地产：主指标用 PB
PB_PRIMARY = {"801010", "801030", "801040", "801050", "801180", "801710",
              "801780", "801790", "801950", "801960"}


def primary_metric(code: str) -> str:
    return "pb" if code in PB_PRIMARY else "pe"


def rolling_pct(values: list[float | None], idx: int) -> float | None:
    """values[idx] 在过去 WIN_DAYS 个位置（含自身）的有效值中的分位，对齐 pandas
    rolling(WIN_DAYS, min_periods=MIN_DAYS).rank(pct=True)；非正值视为缺失。"""
    cur = values[idx]
    if cur is None or cur <= 0:
        return None
    window = [v for v in values[max(0, idx - WIN_DAYS + 1): idx + 1] if v is not None and v > 0]
    if len(window) < MIN_DAYS:
        return None
    return _rank_pct(window, cur)


def label(pct: float | None) -> str | None:
    if pct is None:
        return None
    if pct < LOW:
        return "低位"
    if pct > HIGH:
        return "高位"
    return "中位"


def _quantile(sorted_vals: list[float], q: float) -> float:
    """线性插值分位（同 numpy 默认口径）。"""
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def rolling_bands(values: list[float | None]) -> list[dict[str, float | None]]:
    """逐点的滚动分位带：窗口与 rolling_pct 一致（过去 WIN_DAYS 个位置的正值），
    lo / hi 是窗口的 LOW / HIGH 分位对应的估值，pct 是当点分位。
    区域边界随窗口滚动，与表格里的分位标签同一口径；有效值不足 MIN_DAYS 时全为 None。"""
    window: list[float] = []  # 当前窗口内的正值，保持有序
    out: list[dict[str, float | None]] = []
    for i, v in enumerate(values):
        if v is not None and v > 0:
            insort(window, v)
        if i >= WIN_DAYS:
            old = values[i - WIN_DAYS]
            if old is not None and old > 0:
                del window[bisect_left(window, old)]
        if len(window) < MIN_DAYS:
            out.append({"lo": None, "hi": None, "pct": None})
            continue
        pct = None
        if v is not None and v > 0:
            less = bisect_left(window, v)
            equal = bisect_right(window, v) - less
            pct = (less + (equal + 1) / 2) / len(window)
        out.append({"lo": _quantile(window, LOW), "hi": _quantile(window, HIGH), "pct": pct})
    return out
