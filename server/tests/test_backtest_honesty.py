"""P2 回测诚实度：涨跌停可交易性、事件模式账户口径、组合模式实际换手成本。

先于引擎改动编写（红→绿），期望值全部独立推导。
"""

from statistics import mean

import pytest

from app.services import backtest
from tests.conftest import add_stock, trading_days

GOLDEN = [{"type": "ma_cross", "fast": 2, "slow": 3, "direction": "golden"}]
EVENT_DSL = {"filters": [], "technical": GOLDEN}
SCALAR_DSL = {"filters": [{"factor": "pe", "op": "lte", "value": 100}], "technical": []}

# 金叉信号发生在 idx=5（前段下行 → 反转上行），信号次日 idx=6 入场
BASE = [20, 18, 16, 14, 14, 18, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35]


@pytest.fixture(autouse=True)
def _no_benchmark(monkeypatch):
    monkeypatch.setattr(backtest, "_benchmark_closes", lambda code: {})


def _one_word_board(closes: list[float], idx: int, direction: int) -> tuple:
    """把第 idx 根改造成一字板：open=high=low=close=prev*(1±10%)。返回 (opens, highs, lows, closes)。"""
    o, h, lo, c = list(closes), list(closes), list(closes), list(closes)
    px = round(closes[idx - 1] * (1 + 0.10 * direction), 2)
    o[idx] = h[idx] = lo[idx] = c[idx] = px
    # 后续 close 从新价位续走，保持趋势方向不受影响
    return o, h, lo, c


class TestLimitBoard:
    def test_entry_skipped_after_three_limit_up_days(self, db):
        days = trading_days("2026-01-05", len(BASE))
        closes = list(BASE)
        opens, highs, lows = list(closes), list(closes), list(closes)
        # 信号日 idx=5，其后 6/7/8 三天全部一字涨停 → 放弃该信号
        for j in (6, 7, 8):
            px = round(closes[j - 1] * 1.10, 2)
            closes[j] = opens[j] = highs[j] = lows[j] = px
        add_stock(db, "600519", days, closes, opens=opens, highs=highs, lows=lows)
        result = backtest.run(db, EVENT_DSL, {"holdDays": 5, "costRate": 0})
        assert result["metrics"].get("skippedByLimit", 0) >= 1
        assert all(t["entryDate"] != days[6].strftime("%Y-%m-%d") for t in result["trades"])

    def test_entry_postponed_one_limit_day(self, db):
        days = trading_days("2026-01-05", len(BASE))
        closes = list(BASE)
        opens, highs, lows = list(closes), list(closes), list(closes)
        px = round(closes[5] * 1.10, 2)  # 仅 idx=6 一字板，idx=7 恢复可交易
        closes[6] = opens[6] = highs[6] = lows[6] = px
        add_stock(db, "600519", days, closes, opens=opens, highs=highs, lows=lows)
        result = backtest.run(db, EVENT_DSL, {"holdDays": 5, "entry": "open", "costRate": 0})
        trades = [t for t in result["trades"]]
        assert trades, "顺延一天后应该成交"
        assert trades[0]["entryDate"] == days[7].strftime("%Y-%m-%d")

    def test_exit_postponed_on_limit_down(self, db):
        days = trading_days("2026-01-05", len(BASE))
        closes = list(BASE)
        opens, highs, lows = list(closes), list(closes), list(closes)
        # 持有 3 天：入场 idx=6，正常退出 idx=9 —— 把 idx=9 做成一字跌停，10 恢复
        px = round(closes[8] * 0.90, 2)
        closes[9] = opens[9] = highs[9] = lows[9] = px
        closes[10] = round(px * 1.02, 2)  # 恢复后小涨，10 起接回原趋势
        for j in range(11, len(closes)):
            closes[j] = round(closes[j - 1] * 1.01, 2)
        opens[10:] = closes[10:]
        highs[10:] = closes[10:]
        lows[10:] = closes[10:]
        add_stock(db, "600519", days, closes, opens=opens, highs=highs, lows=lows)
        result = backtest.run(db, EVENT_DSL, {"holdDays": 3, "costRate": 0})
        trades = result["trades"]
        assert trades
        assert trades[0]["exitDate"] == days[10].strftime("%Y-%m-%d")  # 跌停日卖不出，顺延一天


class TestAccountMode:
    def test_max_concurrent_caps_positions(self, db):
        days = trading_days("2026-01-05", len(BASE))
        # 三只股票同一天金叉 → maxConcurrent=2 时只执行前两只（按代码排序）
        for code in ("600000", "600036", "600519"):
            add_stock(db, code, days, list(BASE))
        result = backtest.run(db, EVENT_DSL, {"holdDays": 5, "maxConcurrent": 2, "costRate": 0})
        m = result["metrics"]
        assert m["eventCount"] == 2
        assert m.get("skippedByCapacity", 0) == 1
        codes = {t["code"] for t in result["trades"]}
        assert codes == {"600000", "600036"}

    def test_equity_curve_is_account_based(self, db):
        days = trading_days("2026-01-05", len(BASE))
        add_stock(db, "600519", days, list(BASE))
        max_c = 4
        result = backtest.run(
            db, EVENT_DSL, {"holdDays": 5, "entry": "open", "maxConcurrent": max_c, "costRate": 0}
        )
        # 单笔交易、资金 1/4 仓位：账户总收益 = 每日仓位收益按 1/maxC 加权的累乘
        e_idx, hold = 6, 5
        rs = [BASE[e_idx] / BASE[e_idx] - 1]  # entry=open，open==close → 入场日 0
        for j in range(e_idx + 1, e_idx + hold + 1):
            rs.append(BASE[j] / BASE[j - 1] - 1)
        equity = 1.0
        for r in rs:
            equity *= 1 + r / max_c
        assert result["metrics"]["totalReturn"] == pytest.approx(
            round((equity - 1) * 100, 2), abs=0.05
        )


class TestTurnoverCost:
    def test_stable_holdings_pay_no_rebalance_cost(self, db):
        days = trading_days("2026-01-05", 40)  # 跨月 → 一次调仓，成分完全不变
        add_stock(db, "600519", days, [100 * 1.01**i for i in range(40)])
        add_stock(db, "600000", days, [50 * 1.01**i for i in range(40)])
        rate = 0.001
        result = backtest.run(db, SCALAR_DSL, {"rebalance": "monthly", "costRate": rate})

        # 期望：首期建仓付买入单边 rate；月初调仓成分不变 → 换手 0 → 成本 0。
        # 两只都是日 +1%（等权收益恒 1%），首个计算日叠加建仓成本。
        equity = (1 + 0.01 - rate) * (1.01 ** (len(days) - 2))
        assert result["metrics"]["totalReturn"] == pytest.approx(
            round((equity - 1) * 100, 2), abs=0.1
        )

    def test_rebalance_cost_helper(self):
        rate = 0.001
        # 全换手：卖 1 + 买 1 → 双边
        assert backtest._rebalance_cost({"A": 1.0}, {"B": 1.0}, rate) == pytest.approx(2 * rate)
        # 成分不变 → 零成本
        assert backtest._rebalance_cost({"A": 0.5, "B": 0.5}, {"A": 1.0, "B": 1.0}, rate) == 0
        # 半仓换手：卖 0.5 + 买 0.5 → 单边
        assert backtest._rebalance_cost(
            {"A": 1.0, "B": 1.0}, {"A": 1.0, "C": 1.0}, rate
        ) == pytest.approx(rate)
        # 首期建仓：只有买入侧
        assert backtest._rebalance_cost({}, {"A": 1.0}, rate) == pytest.approx(rate)
