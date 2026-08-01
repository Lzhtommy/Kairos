"""技术形态解释器：signal_series 与 point-in-time passes 的一致性 + 语义。"""

from app.services.technical import bars_needed, passes, reverse_signal, signal_series

GOLDEN = [{"type": "ma_cross", "fast": 2, "slow": 3, "direction": "golden"}]


def test_signal_series_matches_pointwise_passes():
    closes = [10, 9.5, 9, 8.5, 8.6, 9.2, 10.5, 11, 10.8, 10.2, 9.7, 9.5, 10.1, 11.2]
    for tech in (
        GOLDEN,
        [{"type": "ma_rising", "windows": [2, 3]}],
        [{"type": "ma_distance", "fast": 2, "base": 5, "min_pct": -5, "max_pct": 5}],
        [{"type": "ma_trend", "window": 3, "lookback": 8, "max_down_days": 2, "min_gain_pct": 0}],
    ):
        series = signal_series(tech, closes)
        for i in range(len(closes)):
            assert series[i] == passes(tech, closes[: i + 1]), (tech[0]["type"], i)


def test_golden_cross_first_day_only():
    # 前段下行让 MA2 < MA3，随后连续上涨：金叉只在穿越首日为 True
    closes = [10, 9, 8, 7, 7, 9, 11, 12, 13, 14]
    sig = signal_series(GOLDEN, closes)
    assert sum(sig) == 1
    day = sig.index(True)
    assert passes(GOLDEN, closes[: day + 1])
    assert not passes(GOLDEN, closes[: day + 2])  # 次日已在上方，不再是"刚金叉"


def test_reverse_signal_flips_direction():
    rev = reverse_signal(GOLDEN)
    assert rev is not None and rev[0]["direction"] == "death"
    assert reverse_signal([{"type": "ma_rising", "windows": [3]}]) is None


def test_bars_needed():
    assert bars_needed(GOLDEN) == 4  # slow + 1
    assert bars_needed([{"type": "ma_trend", "window": 3, "lookback": 100,
                         "max_down_days": 5, "min_gain_pct": 0}]) == 100
