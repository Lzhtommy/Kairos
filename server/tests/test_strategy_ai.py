"""规则兜底解析器的回归测试（DeepSeek 主路径不在此覆盖）。"""

from app.services.strategy_ai import rule_based


class TestMultiDayParsing:
    def test_streak_with_amplitude_not_double_parsed(self):
        """"每天涨幅超过2%"是逐日幅度，不能同时误产出当日 change_pct 条件。"""
        r = rule_based("连涨5天，每天涨幅超过2%")
        assert r["filters"] == []
        assert r["technical"] == [
            {"type": "daily_change", "days": 5, "min": 2.0, "max": 100.0}
        ]

    def test_cum_change_up(self):
        r = rule_based("近5日累计涨幅超过10%")
        assert r["filters"] == []
        assert r["technical"] == [
            {"type": "cum_change", "days": 5, "min": 10.0, "max": 1000.0}
        ]

    def test_cum_change_down_and_cap(self):
        r = rule_based("近20日跌幅超过15%")
        assert r["technical"] == [
            {"type": "cum_change", "days": 20, "min": -1000.0, "max": -15.0}
        ]
        # "不超过"方向反转：涨幅不超过 5% → max=5
        r = rule_based("近10日累计涨幅不超过5%")
        assert r["technical"] == [
            {"type": "cum_change", "days": 10, "min": -1000.0, "max": 5.0}
        ]

    def test_single_day_change_still_scalar(self):
        r = rule_based("涨跌幅大于3%，PE低于20")
        assert {"factor": "change_pct", "op": "gte", "value": 3.0} in r["filters"]
        assert r["technical"] == []


class TestBuildDslBoardRemap:
    """模型把板块名当行业/中文 board 输出时的矫正。"""

    def test_board_word_in_industry_moved_to_universe(self):
        from app.services.strategy_ai import _build_dsl

        dsl = _build_dsl({
            "filters": [
                {"factor": "industry", "op": "eq", "value": "主板"},
                {"factor": "pe", "op": "lte", "value": 30},
            ],
        })
        assert dsl["universe"]["board"] == ["main"]
        assert all(f["factor"] != "industry" for f in dsl["filters"])

    def test_mixed_industry_list_keeps_real_industries(self):
        from app.services.strategy_ai import _build_dsl

        dsl = _build_dsl({
            "filters": [{"factor": "industry", "op": "in", "value": ["主板", "白酒"]}],
        })
        assert dsl["universe"]["board"] == ["main"]
        assert {"factor": "industry", "op": "eq", "value": "白酒"} in dsl["filters"]

    def test_chinese_board_field_mapped(self):
        from app.services.strategy_ai import _build_dsl

        dsl = _build_dsl({
            "filters": [{"factor": "pe", "op": "lte", "value": 30}],
            "board": ["科创板", "chinext"],
        })
        assert dsl["universe"]["board"] == ["chinext", "star"]
