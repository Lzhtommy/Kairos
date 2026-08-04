"""回测引擎：合成数据下两种模式的行为回归。

基准函数被替换为空 → 交易日历退回参照股 600519，因此每个用例都会造一只
600519。所有期望值在用例内用最朴素的循环独立推导，不复用引擎内部函数。
"""

from statistics import mean

import pytest

from app.services import backtest
from app.services.technical import Bars, signal_series
from tests.conftest import add_stock, trading_days

GOLDEN = [{"type": "ma_cross", "fast": 2, "slow": 3, "direction": "golden"}]


@pytest.fixture(autouse=True)
def _no_benchmark(monkeypatch):
    monkeypatch.setattr(backtest, "_benchmark_closes", lambda code: {})


def _event_dsl() -> dict:
    return {"filters": [], "technical": GOLDEN}


def _scalar_dsl() -> dict:
    return {"filters": [{"factor": "pe", "op": "lte", "value": 100}], "technical": []}


class TestEventMode:
    def test_trades_match_signals(self, db):
        days = trading_days("2026-01-05", 30)
        # 下行→上行制造一次金叉；开盘价与收盘价错开以验证入场口径
        closes = [20 - i * 0.5 for i in range(10)] + [15.5 + i * 0.7 for i in range(20)]
        opens = [c - 0.1 for c in closes]
        add_stock(db, "600519", days, closes, opens=opens)

        result = backtest.run(db, _event_dsl(), {"holdDays": 5, "entry": "open", "costRate": 0.001})
        trades = result["trades"]
        assert result["metrics"]["mode"] == "event"
        assert trades, "金叉序列必须产出至少一笔交易"

        sig = signal_series(GOLDEN, Bars(closes))
        first = sig.index(True)
        t = trades[0]
        e_idx = first + 1
        exit_idx = min(e_idx + 5, len(days) - 1)
        assert t["entryDate"] == days[e_idx].strftime("%Y-%m-%d")
        assert t["entryPx"] == round(opens[e_idx], 3)  # entry=open 用次日开盘
        assert t["exitDate"] == days[exit_idx].strftime("%Y-%m-%d")
        expected_ret = closes[exit_idx] / opens[e_idx] - 1 - 2 * 0.001
        assert t["ret"] == round(expected_ret * 100, 2)

    def test_expr_condition_matches_ma_cross(self, db):
        """expr 的 cross_up(ma,ma) 与白名单 ma_cross 语义一致：同一份数据
        产出完全相同的交易序列（expr 走同一条 signal_series 路径）。"""
        days = trading_days("2026-01-05", 30)
        closes = [20 - i * 0.5 for i in range(10)] + [15.5 + i * 0.7 for i in range(20)]
        add_stock(db, "600519", days, closes)
        expr_dsl = {"filters": [], "technical": [
            {"type": "expr", "formula": "cross_up(ma(close, 2), ma(close, 3))"}
        ]}
        got = backtest.run(db, expr_dsl, {"holdDays": 5})
        want = backtest.run(db, _event_dsl(), {"holdDays": 5})
        assert got["metrics"]["mode"] == "event"
        assert got["trades"], "expr 条件必须产出交易"
        assert [t["entryDate"] for t in got["trades"]] == [
            t["entryDate"] for t in want["trades"]
        ]

    def test_stop_loss_exits_early(self, db):
        days = trading_days("2026-01-05", 30)
        # 金叉后第二天开始暴跌，触发 8% 止损
        closes = [20 - i * 0.5 for i in range(10)] + [16, 17, 15, 12, 11, 10] + [10] * 14
        add_stock(db, "600519", days, closes)
        result = backtest.run(
            db, _event_dsl(),
            {"holdDays": 10, "exitRule": "stop", "stopGain": 50, "stopLoss": 8, "costRate": 0},
        )
        assert any(t["reason"] == "stop_loss" for t in result["trades"])

    def test_no_signal_no_trades(self, db):
        days = trading_days("2026-01-05", 20)
        add_stock(db, "600519", days, [10 - i * 0.1 for i in range(20)])  # 一路下行
        result = backtest.run(db, _event_dsl(), {})
        assert result["metrics"]["eventCount"] == 0

    def test_day_filter_blocks_hot_signal(self, db):
        """日级价格条件必须按信号日检查：金叉当天大涨 10% 的信号要被
        "涨幅≤3%" 拦掉，而不是拿回测运行当天的快照值放行。"""
        days = trading_days("2026-01-05", 30)
        # 下行 10 日 → 第 11 日 +10% 制造金叉 → 此后温和 +0.5%/日
        closes = [20 - i * 0.5 for i in range(10)]
        closes.append(round(closes[-1] * 1.10, 3))
        while len(closes) < 30:
            closes.append(round(closes[-1] * 1.005, 3))
        add_stock(db, "600519", days, closes)

        base = backtest.run(db, _event_dsl(), {})
        assert base["metrics"]["eventCount"] > 0, "无日级条件时金叉信号应成交"

        dsl = {
            "filters": [{"factor": "change_pct", "op": "lte", "value": 3}],
            "technical": GOLDEN,
        }
        result = backtest.run(db, dsl, {})
        assert result["metrics"]["eventCount"] == 0  # 信号日涨 10% > 3%，被拦

        # 最高价口径同理：信号日 high 相对前收 +10% > 5%
        dsl_high = {
            "filters": [{"factor": "high_change_pct", "op": "lte", "value": 5}],
            "technical": GOLDEN,
        }
        assert backtest.run(db, dsl_high, {})["metrics"]["eventCount"] == 0


def test_backtest_in_forwards_max_concurrent():
    """maxConcurrent 必须能穿过 API schema 到达引擎——pydantic 会静默丢弃
    未声明字段，漏声明时前端改持仓上限不生效（回退默认 10）。"""
    from app.schemas import BacktestIn

    body = BacktestIn(strategyId=1, maxConcurrent=50)
    p = backtest._clean_params(body.model_dump(exclude={"strategyId"}), {"cost": {}})
    assert p["max_concurrent"] == 50


class TestPortfolioMode:
    def test_equal_weight_equity_math(self, db):
        days = trading_days("2026-01-05", 40)  # 跨 1 月/2 月 → 一次月度调仓
        up = [100 * 1.01**i for i in range(40)]     # 日 +1%
        down = [100 * 0.99**i for i in range(40)]   # 日 -1%
        add_stock(db, "600519", days, up)
        add_stock(db, "600000", days, down)
        rate = 0.001

        result = backtest.run(
            db, _scalar_dsl(), {"rebalance": "monthly", "costRate": rate, "weighting": "equal"}
        )
        m = result["metrics"]
        assert m["mode"] == "portfolio"
        assert m["hitCount"] == 2
        assert m["rebalances"] == 2  # 首期建仓 + 2 月初一次

        # 独立推导：等权日收益 = mean(+1%, -1%)；成本按实际换手——
        # 首期建仓买入单边 rate（记在第一个计算日），月初调仓成分不变 → 0
        equity = 1.0
        for i in range(1, len(days)):
            r = mean([up[i] / up[i - 1] - 1, down[i] / down[i - 1] - 1])
            if i == 1:
                r -= rate
            equity *= 1 + r
        assert m["totalReturn"] == pytest.approx(round((equity - 1) * 100, 2), abs=0.05)

    def test_max_positions_caps_holdings(self, db):
        days = trading_days("2026-01-05", 15)
        for i, code in enumerate(["600519", "600000", "600036"]):
            add_stock(db, code, days, [10.0 + i] * 15, market_cap=100.0 * (i + 1))
        result = backtest.run(db, _scalar_dsl(), {"maxPositions": 2})
        assert all(rec["holdings"] <= 2 for rec in result["rebalanceRecords"])

    def test_filter_excludes(self, db):
        days = trading_days("2026-01-05", 15)
        add_stock(db, "600519", days, [10.0] * 15, pe=5)
        add_stock(db, "600000", days, [10.0] * 15, pe=500)  # 超出 pe<=100
        result = backtest.run(db, _scalar_dsl(), {})
        assert result["metrics"]["hitCount"] == 1
