"""技术形态解释器：signal_series 与 point-in-time passes 的一致性 + 语义。"""

from app.services.technical import Bars, bars_needed, passes, reverse_signal, signal_series

GOLDEN = [{"type": "ma_cross", "fast": 2, "slow": 3, "direction": "golden"}]


def test_signal_series_matches_pointwise_passes():
    closes = [10, 9.5, 9, 8.5, 8.6, 9.2, 10.5, 11, 10.8, 10.2, 9.7, 9.5, 10.1, 11.2]
    for tech in (
        GOLDEN,
        [{"type": "ma_rising", "windows": [2, 3]}],
        [{"type": "ma_distance", "fast": 2, "base": 5, "min_pct": -5, "max_pct": 5}],
        [{"type": "ma_trend", "window": 3, "lookback": 8, "max_down_days": 2, "min_gain_pct": 0}],
    ):
        series = signal_series(tech, Bars(closes))
        for i in range(len(closes)):
            assert series[i] == passes(tech, Bars(closes[: i + 1])), (tech[0]["type"], i)


def test_golden_cross_first_day_only():
    # 前段下行让 MA2 < MA3，随后连续上涨：金叉只在穿越首日为 True
    closes = [10, 9, 8, 7, 7, 9, 11, 12, 13, 14]
    sig = signal_series(GOLDEN, Bars(closes))
    assert sum(sig) == 1
    day = sig.index(True)
    assert passes(GOLDEN, Bars(closes[: day + 1]))
    assert not passes(GOLDEN, Bars(closes[: day + 2]))  # 次日已在上方，不再是"刚金叉"


def test_reverse_signal_flips_direction():
    rev = reverse_signal(GOLDEN)
    assert rev is not None and rev[0]["direction"] == "death"
    assert reverse_signal([{"type": "ma_rising", "windows": [3]}]) is None


def test_bars_needed():
    assert bars_needed(GOLDEN) == 4  # slow + 1
    # lookback 个均线值需要 window+lookback-1 根日 K
    assert bars_needed([{"type": "ma_trend", "window": 3, "lookback": 100,
                         "max_down_days": 5, "min_gain_pct": 0}]) == 102


def test_ma_trend_lookback_is_ma_value_count():
    """lookback = 均线值个数，与 window 相互独立（lookback < window 曾触发越界）。"""
    cond = [{"type": "ma_trend", "window": 5, "lookback": 3,
             "max_down_days": 0, "min_gain_pct": 0.0}]
    # 单调上涨：MA5 处处上行，自第 window+lookback-2 根起信号为 True
    up = [float(10 + i) for i in range(12)]
    sig = signal_series(cond, Bars(up))
    first = 5 + 3 - 2  # window + lookback - 2
    assert sig[:first] == [False] * first and all(sig[first:])
    # 末端下跌一根：最近 3 个 MA5 值含一次回调，max_down_days=0 不通过
    assert not passes(cond, Bars(up[:-1] + [up[-2] - 5]))
    # 回归：window=60, lookback=5（旧语义下越界崩溃）不再抛错
    long = [10 + i * 0.01 for i in range(200)]
    assert passes([{"type": "ma_trend", "window": 60, "lookback": 5,
                    "max_down_days": 0, "min_gain_pct": 0.0}], Bars(long))
