"""P3：截面算子（rank/pct）、多因子打分、量能/MACD/RSI 技术形态。"""

import pytest

from app.services.dsl import DSLError, execute, validate_dsl
from app.services.technical import passes, signal_series
from tests.test_dsl import _dsl, _row


class TestCrossOps:
    ROWS = [_row(f"6000{i:02d}", pe=float(i + 1)) for i in range(10)]  # pe 1..10

    def test_pct_bottom(self):
        got = execute(_dsl([{"factor": "pe", "op": "pct_bottom", "value": 20}]), self.ROWS)
        assert sorted(r["pe"] for r in got) == [1.0, 2.0]  # 最低 20% = 2 只

    def test_rank_top(self):
        got = execute(_dsl([{"factor": "pe", "op": "rank_top", "value": 3}]), self.ROWS)
        assert sorted(r["pe"] for r in got) == [8.0, 9.0, 10.0]

    def test_rank_scope_industry(self):
        rows = [
            _row("A", industry="白酒", roe=20), _row("B", industry="白酒", roe=10),
            _row("C", industry="证券", roe=8), _row("D", industry="证券", roe=5),
        ]
        got = execute(
            _dsl([{"factor": "roe", "op": "rank_top", "value": 1, "scope": "industry"}]), rows
        )
        assert sorted(r["code"] for r in got) == ["A", "C"]  # 各行业第一名

    def test_cross_combines_with_plain_filter(self):
        # 截面在 universe 全体上算，再与普通条件求交
        got = execute(
            _dsl([
                {"factor": "pe", "op": "pct_bottom", "value": 50},  # pe 1..5
                {"factor": "pe", "op": "gte", "value": 3},
            ]),
            self.ROWS,
        )
        assert sorted(r["pe"] for r in got) == [3.0, 4.0, 5.0]

    def test_none_never_passes(self):
        rows = [*self.ROWS, _row("600099", pe=None)]
        got = execute(_dsl([{"factor": "pe", "op": "pct_bottom", "value": 100}]), rows)
        assert all(r["code"] != "600099" for r in got)

    def test_validation(self):
        with pytest.raises(DSLError):
            validate_dsl(_dsl([{"factor": "pe", "op": "pct_bottom", "value": 120}]))
        with pytest.raises(DSLError):
            validate_dsl(_dsl([{"factor": "pe", "op": "rank_top", "value": 5, "scope": "galaxy"}]))
        with pytest.raises(DSLError):
            validate_dsl(_dsl([{"factor": "industry", "op": "rank_top", "value": 5}]))


class TestScore:
    def test_weighted_rank_score(self):
        rows = [
            _row("A", roe=30, pe=50),   # roe 最好、pe 最差
            _row("B", roe=20, pe=10),   # 双中游偏好
            _row("C", roe=10, pe=30),
        ]
        dsl = {
            "filters": [],
            "technical": [],
            "score": {
                "factors": [
                    {"factor": "roe", "weight": 0.5, "direction": "desc"},
                    {"factor": "pe", "weight": 0.5, "direction": "asc"},
                ],
                "top_n": 2,
            },
        }
        got = execute(dsl, rows)
        assert len(got) == 2
        assert got[0]["code"] == "B"  # roe 第二(0.5) + pe 第一(1.0) → 0.75 最高
        assert all("score" in r for r in got)
        assert got[0]["score"] >= got[1]["score"]

    def test_weights_normalized_and_validation(self):
        out = validate_dsl({
            "filters": [], "technical": [],
            "score": {"factors": [{"factor": "roe", "weight": 3}, {"factor": "pe", "weight": 1, "direction": "asc"}]},
        })
        ws = [f["weight"] for f in out["score"]["factors"]]
        assert ws == [0.75, 0.25]
        with pytest.raises(DSLError):
            validate_dsl({"filters": [], "technical": [], "score": {"factors": []}})
        with pytest.raises(DSLError):
            validate_dsl({"filters": [], "technical": [],
                          "score": {"factors": [{"factor": "industry"}]}})


class TestNewTechnical:
    def test_vol_surge(self):
        closes = [10.0] * 25
        volumes = [100] * 24 + [300]  # 最后一天放量 3 倍
        t = [{"type": "vol_surge", "window": 20, "ratio": 2.0}]
        tech = validate_dsl(_dsl(technical=t))["technical"]
        assert passes(tech, closes, volumes)
        assert not passes(tech, closes, [100] * 25)
        assert not passes(tech, closes, None)  # 无量能数据 → 不通过

    def test_breakout_first_day_only(self):
        closes = [10.0] * 10 + [11.0, 11.0]  # 第 10 天突破前 10 日高点，第 11 天平台
        tech = validate_dsl(_dsl(technical=[{"type": "breakout", "window": 10}]))["technical"]
        sig = signal_series(tech, closes)
        assert sig[10] is True
        assert sig[11] is False  # 11 与前高持平，不再是"创新高"

    def test_rsi_range_oversold(self):
        down = [100 - i for i in range(30)]  # 连跌 → RSI ≈ 0
        up = [100 + i for i in range(30)]    # 连涨 → RSI ≈ 100
        tech = validate_dsl(
            _dsl(technical=[{"type": "rsi_range", "window": 14, "min": 0, "max": 30}])
        )["technical"]
        assert passes(tech, [float(x) for x in down])
        assert not passes(tech, [float(x) for x in up])

    def test_macd_cross_fires_on_reversal(self):
        closes = [float(100 - i) for i in range(40)] + [float(60 + i * 2) for i in range(40)]
        tech = validate_dsl(
            _dsl(technical=[{"type": "macd_cross", "fast": 12, "slow": 26, "signal": 9}])
        )["technical"]
        sig = signal_series(tech, closes)
        assert any(sig[40:]), "下跌转上涨后应出现 MACD 金叉"
        # 一致性：passes == signal_series 最后一位
        for cut in (50, 60, 79):
            assert passes(tech, closes[: cut + 1]) == sig[cut]
