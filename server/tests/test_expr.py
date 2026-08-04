"""expr 公式层：校验白名单、取数窗口推导、逐日求值语义。"""

import pytest

from app.services.expr import ExprError, bars_needed, evaluate, parse


def _s(closes, **kw):
    """构造变量序列：缺省 open/high/low = close，volume = 100。"""
    n = len(closes)
    return {
        "close": closes,
        "open": kw.get("open", list(closes)),
        "high": kw.get("high", list(closes)),
        "low": kw.get("low", list(closes)),
        "volume": kw.get("volume", [100] * n),
    }


class TestValidate:
    def test_ok(self):
        parse("(max(high, 5) - min(low, 5)) / shift(close, 5) * 100 >= 15")
        parse("pct(open, shift(close, 1)) <= -2 and close > open")
        parse("not (close > ma(close, 5)) or volume / ma(volume, 5) > 2")
        parse("-2 <= pct(close, open) <= 5")  # 链式比较

    @pytest.mark.parametrize(
        "formula",
        [
            "close >",  # 语法错误
            "close",  # 顶层不是布尔
            "close + open",  # 顶层不是布尔
            "close ** 2 > 1",  # 幂运算被禁
            "__import__('os') > 1",  # 未知函数
            "close[0] > 1",  # 下标
            "close.real > 1",  # 属性
            "foo(close, 5) > 1",  # 未知函数
            "ma(close, n) > 1",  # 窗口非字面量
            "ma(close, 2 + 3) > 1",  # 窗口非字面量
            "ma(close, 0) > 1",  # 窗口越界
            "ma(close, 241) > 1",  # 窗口越界
            "ma(close, 5.5) > 1",  # 窗口非整数
            "shift(close) > 1",  # 参数个数
            "unknown_var > 1",  # 未知变量
            "'abc' > close",  # 字符串字面量
            "(close > 1) + 1 > 0",  # 布尔参与四则
            "ma(close > 1, 5) > 1",  # ma 的参数必须是数值
            "all(close, 5)",  # all 的参数必须是布尔
            "close > 1 and volume",  # and 两侧必须是布尔
        ],
    )
    def test_rejected(self, formula):
        with pytest.raises(ExprError):
            parse(formula)

    def test_too_long_and_too_complex(self):
        with pytest.raises(ExprError):
            parse("close > 1 or " * 40 + "close > 1")  # 超长
        with pytest.raises(ExprError):
            parse(" + ".join(["close"] * 60) + " > 1")  # 节点数超限


class TestBarsNeeded:
    def test_derivation(self):
        assert bars_needed("close > 1") == 1
        assert bars_needed("shift(close, 5) > 1") == 6
        assert bars_needed("ma(close, 20) > 1") == 20
        assert bars_needed("ma(shift(close, 5), 20) > 1") == 25
        assert bars_needed("cross_up(ma(close, 5), ma(close, 20))") == 21
        assert bars_needed("all(volume < shift(volume, 1), 3)") == 4
        assert bars_needed("pct(close, max(high, 60)) <= -20") == 60


class TestEvaluate:
    def test_drawdown_from_high(self):
        # 前段冲到 130，尾段跌到 100：回撤 = (100/130-1)*100 ≈ -23%
        closes = [100.0, 110.0, 120.0, 130.0, 120.0, 110.0, 100.0]
        sig = evaluate("pct(close, max(high, 5)) <= -20", _s(closes))
        assert sig[-1] is True
        assert sig[3] is False  # 高点当日回撤 0

    def test_low_open_up_close(self):
        closes = [100.0, 101.0]
        opens = [100.0, 97.0]  # 次日低开 4%、收 101 高走
        sig = evaluate("pct(open, shift(close, 1)) <= -2 and close > open", _s(closes, open=opens))
        assert sig == [False, True]

    def test_volume_ratio(self):
        vols = [100] * 5 + [300]
        sig = evaluate("volume / ma(volume, 5) > 2", _s([1.0] * 6, volume=vols))
        assert sig[-1] is True
        assert sig[-2] is False

    def test_cross_up_first_day_only(self):
        closes = [10.0, 9.0, 8.0, 7.0, 7.0, 9.0, 11.0, 12.0, 13.0, 14.0]
        sig = evaluate("cross_up(ma(close, 2), ma(close, 3))", _s(closes))
        assert sum(sig) == 1  # 只认穿越首日

    def test_all_and_count(self):
        vols = [500, 400, 300, 200, 100]
        s = _s([1.0] * 5, volume=vols)
        assert evaluate("all(volume < shift(volume, 1), 3)", s)[-1] is True
        assert evaluate("count(volume < shift(volume, 1), 4) == 4", s)[-1] is True

    def test_none_semantics(self):
        closes = [100.0, 105.0]
        # 窗口不足 → False；除零 → False
        assert evaluate("ma(close, 5) > 1", _s(closes)) == [False, False]
        assert evaluate("close / (close - close) > 1", _s(closes)) == [False, False]
        # 价格 0 视为缺值：open 缺列时引用 open 的公式全 False
        s = _s(closes, open=[0.0, 0.0])
        assert evaluate("close > open", s) == [False, False]

    def test_std_and_ema(self):
        closes = [10.0, 12.0, 14.0, 16.0]
        sig = evaluate("std(close, 3) > 1", _s(closes))
        assert sig == [False, False, True, True]  # std([10,12,14]) ≈ 1.63
        assert evaluate("ema(close, 3) > 12", _s(closes))[-1] is True

    def test_point_in_time_consistency(self):
        # signal_series 语义：第 i 日结果只依赖 ≤i 的数据
        closes = [float(100 + ((i * 7) % 13) - 6) for i in range(40)]
        formula = "close > ma(close, 5) and volume / ma(volume, 5) > 0.5"
        vols = [100 + (i * 11) % 50 for i in range(40)]
        full = evaluate(formula, _s(closes, volume=vols))
        for cut in (10, 20, 39):
            part = evaluate(formula, _s(closes[: cut + 1], volume=vols[: cut + 1]))
            assert part[cut] == full[cut]
