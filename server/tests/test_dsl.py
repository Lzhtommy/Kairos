"""DSL 校验与执行的回归测试。"""

import pytest

from app.services.dsl import DSLError, execute, validate_dsl


def _dsl(filters=None, technical=None) -> dict:
    return {"filters": filters or [], "technical": technical or []}


def _row(code="600000", **kw) -> dict:
    base = {
        "code": code, "name": f"股票{code}", "market": "SH", "industry": "白酒",
        "pe": 10.0, "pb": 2.0, "roe": 15.0, "turnover_rate": 1.0, "turnover": 5.0,
        "market_cap": 100.0, "change_pct": 1.0, "high_change_pct": 2.0,
        "low_change_pct": -1.0, "open_change_pct": 0.5,
        "price": 10.0, "dividend_yield": 0.02,
    }
    base.update(kw)
    return base


class TestValidate:
    def test_unknown_factor_rejected(self):
        with pytest.raises(DSLError):
            validate_dsl(_dsl([{"factor": "magic", "op": "gt", "value": 1}]))

    def test_bad_op_for_kind(self):
        with pytest.raises(DSLError):
            validate_dsl(_dsl([{"factor": "industry", "op": "gt", "value": "白酒"}]))
        with pytest.raises(DSLError):
            validate_dsl(_dsl([{"factor": "pe", "op": "in", "value": [1, 2]}]))

    def test_both_empty_rejected(self):
        with pytest.raises(DSLError):
            validate_dsl(_dsl())

    def test_between_requires_min_max(self):
        with pytest.raises(DSLError):
            validate_dsl(_dsl([{"factor": "pe", "op": "between", "min": 1}]))
        out = validate_dsl(_dsl([{"factor": "pe", "op": "between", "min": 5, "max": 20}]))
        assert out["filters"][0] == {"factor": "pe", "op": "between", "min": 5.0, "max": 20.0}

    def test_technical_params_clamped(self):
        out = validate_dsl(_dsl(technical=[{"type": "ma_cross", "fast": 3, "slow": 9999}]))
        assert out["technical"][0]["slow"] == 120  # SPECS 上限
        with pytest.raises(DSLError):
            validate_dsl(_dsl(technical=[{"type": "ma_cross", "fast": 10, "slow": 5}]))

    def test_daily_change_validate(self):
        out = validate_dsl(
            _dsl(technical=[{"type": "daily_change", "days": 3, "min": 2, "max": 100}])
        )
        assert out["technical"][0] == {"type": "daily_change", "days": 3, "min": 2.0, "max": 100.0}
        with pytest.raises(DSLError):  # min > max
            validate_dsl(_dsl(technical=[{"type": "daily_change", "days": 3, "min": 5, "max": 2}]))

    def test_defaults_filled(self):
        out = validate_dsl(_dsl([{"factor": "pe", "op": "lte", "value": 30}]))
        assert out["universe"]["market"] == ["SH", "SZ"]
        assert out["cost"]["rate"] == 0.0005


class TestExecute:
    def test_numeric_ops(self):
        rows = [_row("A", pe=5), _row("B", pe=15), _row("C", pe=None)]
        got = execute(_dsl([{"factor": "pe", "op": "lte", "value": 10}]), rows)
        assert [r["code"] for r in got] == ["A"]  # None（亏损）不通过数值筛

    def test_high_change_pct(self):
        rows = [
            _row("A", high_change_pct=6.2),
            _row("B", high_change_pct=3.0),
            _row("C", high_change_pct=None),  # 缺 K 线 / 开盘前
        ]
        got = execute(_dsl([{"factor": "high_change_pct", "op": "gte", "value": 5}]), rows)
        assert [r["code"] for r in got] == ["A"]

    def test_low_change_pct(self):
        # 盘中最多跌 2% 以内：low_change_pct >= -2
        rows = [
            _row("A", low_change_pct=-1.5),
            _row("B", low_change_pct=-5.0),
            _row("C", low_change_pct=None),
        ]
        got = execute(_dsl([{"factor": "low_change_pct", "op": "gte", "value": -2}]), rows)
        assert [r["code"] for r in got] == ["A"]

    def test_open_change_pct(self):
        # 高开：open_change_pct > 0
        rows = [
            _row("A", open_change_pct=2.1),
            _row("B", open_change_pct=-0.8),
            _row("C", open_change_pct=None),  # 未开盘 / 缺 K 线
        ]
        got = execute(_dsl([{"factor": "open_change_pct", "op": "gt", "value": 0}]), rows)
        assert [r["code"] for r in got] == ["A"]


class TestDailyChange:
    def test_streak_semantics(self):
        from app.services.technical import passes, signal_series

        # 连续 3 日每天 +2%：末段三天 +3% 满足 min=2，前面的下行日不满足
        closes = [100.0, 99.0, 98.0, 100.94, 103.97, 107.09]
        cond = [{"type": "daily_change", "days": 3, "min": 2.0, "max": 100.0}]
        sig = signal_series(cond, closes)
        assert sig[-1] is True  # 最近 3 天都 +3%
        assert sig[-2] is False  # 窗口含 98→100.94 前的下跌日？含 99→98(-1%) → 不满足
        assert passes(cond, closes) is True

        # 连跌 2 天（max=0）：末两天下跌成立
        down = [100.0, 101.0, 100.0, 99.0]
        assert passes([{"type": "daily_change", "days": 2, "min": -100.0, "max": 0.0}], down)
        # 数据不足（需要 days+1 根）不通过
        assert not passes([{"type": "daily_change", "days": 3, "min": -100.0, "max": 0.0}], down[:3])

    def test_cum_change_semantics(self):
        from app.services.technical import passes

        # 窗口含今日共 days 根：days=5 时基准 = 倒数第 5 根的开盘价
        closes = [100.0, 102.0, 104.0, 106.0, 108.0, 111.0]
        opens = [99.0, 100.0, 101.0, 103.0, 105.0, 107.0]  # 今收 111 对 opens[1]=100 → +11%
        cond = {"type": "cum_change", "days": 5, "min": 10.0, "max": 1000.0}
        assert passes([cond], closes, opens=opens)
        assert not passes([{**cond, "min": 12.0}], closes, opens=opens)
        # days=3 → 今收对前天开盘：111 / 103 - 1 ≈ +7.77%
        assert passes(
            [{"type": "cum_change", "days": 3, "min": 7.0, "max": 8.0}], closes, opens=opens
        )
        # 数据不足（窗口需要 days 根）不通过
        assert not passes(
            [{"type": "cum_change", "days": 7, "min": 0.0, "max": 1000.0}], closes, opens=opens
        )
        # 缺开盘价直接不通过
        assert not passes([cond], closes)

    def test_industry_in(self):
        rows = [_row("A", industry="白酒"), _row("B", industry="证券")]
        got = execute(_dsl([{"factor": "industry", "op": "in", "value": ["证券"]}]), rows)
        assert [r["code"] for r in got] == ["B"]

    def test_industry_median_ref(self):
        rows = [
            _row("A", pe=5), _row("B", pe=10), _row("C", pe=20),
            _row("D", industry="证券", pe=100),
        ]
        got = execute(
            _dsl([{"factor": "pe", "op": "lt", "ref": "industry_median"}]), rows
        )
        assert [r["code"] for r in got] == ["A"]  # 白酒中位数 10，仅 A 低于

    def test_universe_st_and_market(self):
        rows = [_row("A"), _row("B", name="ST股票B"), _row("C", market="BJ")]
        got = execute(_dsl([{"factor": "pe", "op": "gt", "value": 0}]), rows)
        assert [r["code"] for r in got] == ["A"]
