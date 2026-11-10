"""expr 技术条件：白名单 AST 公式的校验与逐日求值。

安全模型与 DSL 其他层一致——不 eval 任何代码。公式用 ast.parse 解析成
表达式树，节点类型 / 函数名 / 变量名三层白名单，其余一律拒绝。滚动窗口
参数必须是整数字面量，换来保存时就能静态推导取数窗口（bars_needed）。

语义约定（与白名单技术条件一致）：
- 滚动窗口一律含当日：ma(close, 5) 在第 i 日 = closes[i-4..i] 均值。
- 缺值语义：窗口不足、除零、任一操作数缺值 → 该日结果 None；
  顶层为 None 的交易日判 False（数据不足不通过，不降级凑数）。
- 价格变量（open/high/low/close）取值 ≤0 视为缺值；volume 的 0 是合法值。
- amount（成交额，亿元）= volume(股) × close / 1e8：日 K 不存成交额，按收盘价
  近似（实测与真实成交额偏差 <1%）。
"""

from __future__ import annotations

import ast
import math
from functools import lru_cache
from typing import Any

import numpy as np

MAX_FORMULA_LEN = 300
MAX_NODES = 100
MAX_DEPTH = 12
MAX_WINDOW = 240

PRICE_VARS = ("open", "high", "low", "close")
VARS = (*PRICE_VARS, "volume", "amount")

# name -> (窗口外参数的类型列表, 是否带窗口参数, 返回类型)；类型: "num" | "bool"
FUNCS: dict[str, tuple[tuple[str, ...], bool, str]] = {
    "shift": (("num",), True, "num"),
    "ma": (("num",), True, "num"),
    "ema": (("num",), True, "num"),
    "sum": (("num",), True, "num"),
    "max": (("num",), True, "num"),
    "min": (("num",), True, "num"),
    "std": (("num",), True, "num"),
    "abs": (("num",), False, "num"),
    "max2": (("num", "num"), False, "num"),
    "min2": (("num", "num"), False, "num"),
    "pct": (("num", "num"), False, "num"),
    "count": (("bool",), True, "num"),
    "all": (("bool",), True, "bool"),
    "any": (("bool",), True, "bool"),
    "cross_up": (("num", "num"), False, "bool"),
    "cross_down": (("num", "num"), False, "bool"),
}

_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div)
_CMPOPS = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)


class ExprError(ValueError):
    """公式非法：语法 / 白名单 / 资源上限 / 类型不匹配。"""


# ---- 校验 --------------------------------------------------------------------


def parse(formula: Any) -> ast.expr:
    """解析 + 全量校验，返回表达式根节点；非法时抛 ExprError。"""
    if not isinstance(formula, str) or not formula.strip():
        raise ExprError("formula 需要非空字符串")
    if len(formula) > MAX_FORMULA_LEN:
        raise ExprError(f"公式过长（>{MAX_FORMULA_LEN} 字符）")
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as e:
        raise ExprError(f"语法错误: {e.msg}") from None
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise ExprError(f"公式过于复杂（节点数 > {MAX_NODES}）")
    root = tree.body
    if _kind(root, 0) != "bool":
        raise ExprError("顶层必须是比较或布尔表达式（每日结果为真/假）")
    return root


def _kind(node: ast.expr, depth: int) -> str:
    """递归校验节点并返回值类型 num/bool。白名单之外一律 ExprError。"""
    if depth > MAX_DEPTH:
        raise ExprError(f"嵌套过深（> {MAX_DEPTH} 层）")
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ExprError("字面量只允许数值")
        return "num"
    if isinstance(node, ast.Name):
        if node.id not in VARS:
            raise ExprError(f"未知变量: {node.id}（可用: {', '.join(VARS)}）")
        return "num"
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, (ast.USub, ast.UAdd)):
            if _kind(node.operand, depth + 1) != "num":
                raise ExprError("正负号只能作用于数值")
            return "num"
        if isinstance(node.op, ast.Not):
            if _kind(node.operand, depth + 1) != "bool":
                raise ExprError("not 只能作用于布尔条件")
            return "bool"
        raise ExprError("不支持的一元运算")
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _BINOPS):
            raise ExprError("只支持 + - * / 四则运算")
        if _kind(node.left, depth + 1) != "num" or _kind(node.right, depth + 1) != "num":
            raise ExprError("四则运算两侧必须是数值")
        return "num"
    if isinstance(node, ast.Compare):
        if any(not isinstance(op, _CMPOPS) for op in node.ops):
            raise ExprError("只支持 > >= < <= == != 比较")
        for sub in (node.left, *node.comparators):
            if _kind(sub, depth + 1) != "num":
                raise ExprError("比较两侧必须是数值")
        return "bool"
    if isinstance(node, ast.BoolOp):
        for sub in node.values:
            if _kind(sub, depth + 1) != "bool":
                raise ExprError("and/or 两侧必须是布尔条件")
        return "bool"
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in FUNCS:
            raise ExprError(f"未知函数（可用: {', '.join(FUNCS)}）")
        if node.keywords:
            raise ExprError("函数不支持关键字参数")
        name = node.func.id
        arg_kinds, has_window, ret = FUNCS[name]
        expected = len(arg_kinds) + (1 if has_window else 0)
        if len(node.args) != expected:
            raise ExprError(f"{name} 需要 {expected} 个参数")
        for a, k in zip(node.args, arg_kinds):
            if _kind(a, depth + 1) != k:
                want = "布尔条件" if k == "bool" else "数值"
                raise ExprError(f"{name} 的参数必须是{want}")
        if has_window:
            w = node.args[-1]
            if (
                not isinstance(w, ast.Constant)
                or isinstance(w.value, bool)
                or not isinstance(w.value, int)
            ):
                raise ExprError(f"{name} 的窗口参数必须是整数字面量")
            if not 1 <= w.value <= MAX_WINDOW:
                raise ExprError(f"{name} 的窗口需在 1..{MAX_WINDOW}")
        return ret
    raise ExprError(f"不支持的语法: {type(node).__name__}")


# ---- 取数窗口推导 -------------------------------------------------------------


def bars_needed(formula: str | ast.expr) -> int:
    """公式所需的最少日 K 根数（静态推导，窗口参数是字面量才有此能力）。"""
    root = parse(formula) if isinstance(formula, str) else formula
    return _need(root)


def _need(node: ast.expr) -> int:
    if isinstance(node, (ast.Constant, ast.Name)):
        return 1
    if isinstance(node, ast.UnaryOp):
        return _need(node.operand)
    if isinstance(node, ast.BinOp):
        return max(_need(node.left), _need(node.right))
    if isinstance(node, ast.Compare):
        return max(_need(sub) for sub in (node.left, *node.comparators))
    if isinstance(node, ast.BoolOp):
        return max(_need(sub) for sub in node.values)
    if isinstance(node, ast.Call):
        name = node.func.id  # type: ignore[union-attr]  # parse 已保证是 Name
        if name == "shift":
            return _need(node.args[0]) + node.args[1].value  # type: ignore[attr-defined]
        if name in ("ma", "ema", "sum", "max", "min", "std", "count", "all", "any"):
            return _need(node.args[0]) + node.args[1].value - 1  # type: ignore[attr-defined]
        if name in ("cross_up", "cross_down"):
            return max(_need(node.args[0]), _need(node.args[1])) + 1
        return max(_need(a) for a in node.args)  # abs / pct / max2 / min2
    raise ExprError(f"不支持的语法: {type(node).__name__}")


# ---- 求值 --------------------------------------------------------------------
#
# 向量化实现：每个节点算出整条 float64 序列，NaN 表示缺值；布尔序列同样用
# float64 承载（1.0 真 / 0.0 假 / NaN 缺值），这样缺值传播和数值完全同一套机制。
# 滚动和 / 均值 / 标准差沿用前缀和差分（np.cumsum 逐项累加，与逐日循环同精度）。

_Series = np.ndarray  # float64，长度 = 日 K 根数


@lru_cache(maxsize=256)
def _parse_cached(formula: str) -> ast.expr:
    # 同一公式会对上千只股票逐只求值，解析 + 校验只做一次（AST 只读不改）
    return parse(formula)


def evaluate(formula: str | ast.expr, series: dict[str, list[float] | None]) -> list[bool]:
    """逐日求值，返回布尔信号序列（None → False）。

    series: 变量名 -> 升序序列，缺整列传 None（引用它的公式全 False）。
    """
    root = _parse_cached(formula) if isinstance(formula, str) else formula
    n = len(series.get("close") or [])
    if n == 0:
        return []
    with np.errstate(all="ignore"):
        out = _eval(root, series, n)
    return (out == 1.0).tolist()


def _nan(n: int) -> _Series:
    return np.full(n, np.nan)


def _bool(cond: np.ndarray, *operands: _Series) -> _Series:
    """布尔结果转三值序列：任一操作数缺值的位置为 NaN。"""
    out = cond.astype(float)
    for o in operands:
        out[np.isnan(o)] = np.nan
    return out


def _eval(node: ast.expr, s: dict[str, list[float] | None], n: int) -> _Series:
    if isinstance(node, ast.Constant):
        return np.full(n, float(node.value))
    if isinstance(node, ast.Name):
        if node.id == "amount":
            vol, close = _eval(ast.Name("volume"), s, n), _eval(ast.Name("close"), s, n)
            return vol * close / 1e8
        raw = s.get(node.id)
        if raw is None or len(raw) != n:
            return _nan(n)
        arr = np.array(raw, dtype=float)  # None → NaN
        if node.id in PRICE_VARS:  # 价格 ≤0 视为缺值（采集缺口存的 0）
            arr[~(arr > 0)] = np.nan
        return arr
    if isinstance(node, ast.UnaryOp):
        vals = _eval(node.operand, s, n)
        if isinstance(node.op, ast.USub):
            return -vals
        if isinstance(node.op, ast.UAdd):
            return vals
        return 1.0 - vals  # Not：1↔0，NaN 保持
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left, s, n), _eval(node.right, s, n)
        op = node.op
        if isinstance(op, ast.Add):
            return a + b
        if isinstance(op, ast.Sub):
            return a - b
        if isinstance(op, ast.Mult):
            return a * b
        out = a / b  # Div；除零 → 缺值
        out[b == 0] = np.nan
        return out
    if isinstance(node, ast.Compare):
        operands = [_eval(sub, s, n) for sub in (node.left, *node.comparators)]
        cond = np.ones(n, dtype=bool)
        for j, op in enumerate(node.ops):
            cond &= _CMP[type(op)](operands[j], operands[j + 1])
        return _bool(cond, *operands)
    if isinstance(node, ast.BoolOp):
        parts = [_eval(sub, s, n) for sub in node.values]
        truth = [p == 1.0 for p in parts]
        cond = np.logical_and.reduce(truth) if isinstance(node.op, ast.And) else np.logical_or.reduce(truth)
        return _bool(cond, *parts)
    # parse 已保证剩下只有白名单函数调用
    return _call(node, s, n)  # type: ignore[arg-type]


_CMP = {
    ast.Lt: np.less,
    ast.LtE: np.less_equal,
    ast.Gt: np.greater,
    ast.GtE: np.greater_equal,
    ast.Eq: np.equal,
    ast.NotEq: np.not_equal,
}


def _call(node: ast.Call, s: dict[str, list[float] | None], n: int) -> _Series:
    name = node.func.id  # type: ignore[union-attr]
    if name == "abs":
        return np.abs(_eval(node.args[0], s, n))
    if name in ("max2", "min2"):
        # 逐日取两序列较大/较小值（区别于滚动窗口的 max/min）；NaN 自动传播
        a, b = _eval(node.args[0], s, n), _eval(node.args[1], s, n)
        return np.maximum(a, b) if name == "max2" else np.minimum(a, b)
    if name == "pct":
        a, b = _eval(node.args[0], s, n), _eval(node.args[1], s, n)
        out = (a / b - 1) * 100
        out[b == 0] = np.nan
        return out
    if name in ("cross_up", "cross_down"):
        a, b = _eval(node.args[0], s, n), _eval(node.args[1], s, n)
        out = _nan(n)
        if n < 2:
            return out
        a0, b0, a1, b1 = a[:-1], b[:-1], a[1:], b[1:]
        if name == "cross_up":
            cond = (a0 <= b0) & (a1 > b1)
        else:
            cond = (a0 >= b0) & (a1 < b1)
        out[1:] = _bool(cond, a0, b0, a1, b1)
        return out

    x = _eval(node.args[0], s, n)
    w: int = node.args[1].value  # type: ignore[attr-defined]  # parse 已保证整数字面量
    if name == "shift":
        out = _nan(n)
        if w < n:
            out[w:] = x[: n - w]
        return out
    if name == "ema":
        return _ema(x, w, n)
    return _rolling(name, x, w, n)


def _ema(x: _Series, w: int, n: int) -> _Series:
    """首个非缺值处播种；中途遇缺值断开、之后重新播种。递推无法向量化，逐日算。"""
    out = _nan(n)
    alpha = 2 / (w + 1)
    ema: float | None = None
    for i, v in enumerate(x.tolist()):
        if math.isnan(v):
            ema = None
            continue
        ema = v if ema is None else alpha * v + (1 - alpha) * ema
        out[i] = ema
    return out


def _rolling(name: str, x: _Series, w: int, n: int) -> _Series:
    """滚动算子。窗口含当日；窗口不足或窗口内有缺值 → NaN。"""
    out = _nan(n)
    if w > n:
        return out
    missing = np.isnan(x)
    # 缺值前缀计数：窗口 [i-w+1, i] 有效 iff i >= w-1 且窗口内无缺值
    miss_pref = np.concatenate(([0], np.cumsum(missing)))
    valid = np.zeros(n, dtype=bool)
    valid[w - 1 :] = miss_pref[w:] == miss_pref[: n - w + 1]

    if name in ("sum", "ma", "std", "count"):
        # count 的输入是布尔序列，真记 1；sum/ma/std 是数值
        v = np.where(missing, 0.0, x)
        pref = np.concatenate(([0.0], np.cumsum(v)))
        total = pref[w:] - pref[: n - w + 1]
        if name in ("sum", "count"):
            res = total
        elif name == "ma":
            res = total / w
        else:  # std（总体标准差）
            pref_sq = np.concatenate(([0.0], np.cumsum(v * v)))
            sq = pref_sq[w:] - pref_sq[: n - w + 1]
            res = np.sqrt(np.maximum(sq / w - (total / w) ** 2, 0.0))
    elif name in ("max", "min"):
        win = np.lib.stride_tricks.sliding_window_view(x, w)
        # 缺值窗口最后由 valid 屏蔽，这里先用哨兵顶替 NaN 避免告警
        fill = -np.inf if name == "max" else np.inf
        win = np.where(np.isnan(win), fill, win)
        res = win.max(axis=1) if name == "max" else win.min(axis=1)
    else:  # all / any：布尔滚动，基于真值计数
        true_pref = np.concatenate(([0], np.cumsum(x == 1.0)))
        cnt = true_pref[w:] - true_pref[: n - w + 1]
        res = (cnt == w if name == "all" else cnt > 0).astype(float)
    out[w - 1 :] = np.where(valid[w - 1 :], res, np.nan)
    return out
