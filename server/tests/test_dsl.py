"""DSL 校验与执行的回归测试。"""

import pytest

from app.services.dsl import DSLError, execute, validate_dsl


def _dsl(filters=None, technical=None) -> dict:
    return {"filters": filters or [], "technical": technical or []}


def _row(code="600000", **kw) -> dict:
    base = {
        "code": code, "name": f"股票{code}", "market": "SH", "industry": "白酒",
        "pe": 10.0, "pb": 2.0, "roe": 15.0, "turnover_rate": 1.0, "turnover": 5.0,
        "market_cap": 100.0, "change_pct": 1.0, "price": 10.0, "dividend_yield": 0.02,
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

    def test_defaults_filled(self):
        out = validate_dsl(_dsl([{"factor": "pe", "op": "lte", "value": 30}]))
        assert out["universe"]["market"] == ["SH", "SZ"]
        assert out["cost"]["rate"] == 0.0005


class TestExecute:
    def test_numeric_ops(self):
        rows = [_row("A", pe=5), _row("B", pe=15), _row("C", pe=None)]
        got = execute(_dsl([{"factor": "pe", "op": "lte", "value": 10}]), rows)
        assert [r["code"] for r in got] == ["A"]  # None（亏损）不通过数值筛

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
