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
