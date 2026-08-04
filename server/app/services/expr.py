"""expr 技术条件：白名单 AST 公式的校验与逐日求值。

安全模型与 DSL 其他层一致——不 eval 任何代码。公式用 ast.parse 解析成
表达式树，节点类型 / 函数名 / 变量名三层白名单，其余一律拒绝。滚动窗口
参数必须是整数字面量，换来保存时就能静态推导取数窗口（bars_needed）。

语义约定（与白名单技术条件一致）：
- 滚动窗口一律含当日：ma(close, 5) 在第 i 日 = closes[i-4..i] 均值。
- 缺值语义：窗口不足、除零、任一操作数缺值 → 该日结果 None；
  顶层为 None 的交易日判 False（数据不足不通过，不降级凑数）。
- 价格变量（open/high/low/close）取值 ≤0 视为缺值；volume 的 0 是合法值。
"""

from __future__ import annotations

import ast
import math
from typing import Any

MAX_FORMULA_LEN = 300
MAX_NODES = 100
MAX_DEPTH = 12
MAX_WINDOW = 240

PRICE_VARS = ("open", "high", "low", "close")
VARS = (*PRICE_VARS, "volume")

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
        return max(_need(a) for a in node.args)  # abs / pct
    raise ExprError(f"不支持的语法: {type(node).__name__}")


# ---- 求值 --------------------------------------------------------------------

_Series = list  # 元素 float | bool | None，长度 = 日 K 根数


def evaluate(formula: str | ast.expr, series: dict[str, list[float] | None]) -> list[bool]:
    """逐日求值，返回布尔信号序列（None → False）。

    series: 变量名 -> 升序序列，缺整列传 None（引用它的公式全 False）。
    """
    root = parse(formula) if isinstance(formula, str) else formula
    n = len(series.get("close") or [])
    if n == 0:
        return []
    out = _eval(root, series, n)
    return [v is True for v in out]


def _eval(node: ast.expr, s: dict[str, list[float] | None], n: int) -> _Series:
    if isinstance(node, ast.Constant):
        return [float(node.value)] * n
    if isinstance(node, ast.Name):
        raw = s.get(node.id)
        if raw is None or len(raw) != n:
            return [None] * n
        if node.id in PRICE_VARS:  # 价格 ≤0 视为缺值（采集缺口存的 0）
            return [v if v is not None and v > 0 else None for v in raw]
        return [float(v) if v is not None else None for v in raw]
    if isinstance(node, ast.UnaryOp):
        vals = _eval(node.operand, s, n)
        if isinstance(node.op, ast.USub):
            return [-v if v is not None else None for v in vals]
        if isinstance(node.op, ast.UAdd):
            return vals
        return [(not v) if v is not None else None for v in vals]  # Not
    if isinstance(node, ast.BinOp):
        lhs, rhs = _eval(node.left, s, n), _eval(node.right, s, n)
        op = node.op
        out: _Series = [None] * n
        for i in range(n):
            a, b = lhs[i], rhs[i]
            if a is None or b is None:
                continue
            if isinstance(op, ast.Add):
                out[i] = a + b
            elif isinstance(op, ast.Sub):
                out[i] = a - b
            elif isinstance(op, ast.Mult):
                out[i] = a * b
            elif b != 0:  # Div；除零 → None
                out[i] = a / b
        return out
    if isinstance(node, ast.Compare):
        operands = [_eval(sub, s, n) for sub in (node.left, *node.comparators)]
        out = [None] * n
        for i in range(n):
            vals = [o[i] for o in operands]
            if any(v is None for v in vals):
                continue
            out[i] = all(
                _cmp(op, vals[j], vals[j + 1]) for j, op in enumerate(node.ops)
            )
        return out
    if isinstance(node, ast.BoolOp):
        parts = [_eval(sub, s, n) for sub in node.values]
        is_and = isinstance(node.op, ast.And)
        out = [None] * n
        for i in range(n):
            vals = [p[i] for p in parts]
            if any(v is None for v in vals):
                continue
            out[i] = all(vals) if is_and else any(vals)
        return out
    # parse 已保证剩下只有白名单函数调用
    return _call(node, s, n)  # type: ignore[arg-type]


def _cmp(op: ast.cmpop, a: float, b: float) -> bool:
    if isinstance(op, ast.Lt):
        return a < b
    if isinstance(op, ast.LtE):
        return a <= b
    if isinstance(op, ast.Gt):
        return a > b
    if isinstance(op, ast.GtE):
        return a >= b
    if isinstance(op, ast.Eq):
        return a == b
    return a != b


def _call(node: ast.Call, s: dict[str, list[float] | None], n: int) -> _Series:
    name = node.func.id  # type: ignore[union-attr]
    if name == "abs":
        x = _eval(node.args[0], s, n)
        return [abs(v) if v is not None else None for v in x]
    if name == "pct":
        a, b = _eval(node.args[0], s, n), _eval(node.args[1], s, n)
        return [
            (a[i] / b[i] - 1) * 100 if a[i] is not None and b[i] not in (None, 0) else None
            for i in range(n)
        ]
    if name in ("cross_up", "cross_down"):
        a, b = _eval(node.args[0], s, n), _eval(node.args[1], s, n)
        up = name == "cross_up"
        out: _Series = [None] * n
        for i in range(1, n):
            if None in (a[i], b[i], a[i - 1], b[i - 1]):
                continue
            if up:
                out[i] = a[i - 1] <= b[i - 1] and a[i] > b[i]
            else:
                out[i] = a[i - 1] >= b[i - 1] and a[i] < b[i]
        return out

    x = _eval(node.args[0], s, n)
    w: int = node.args[1].value  # type: ignore[attr-defined]  # parse 已保证整数字面量
    if name == "shift":
        return [None] * min(w, n) + x[: n - w]
    if name == "ema":
        return _ema(x, w, n)
    return _rolling(name, x, w, n)


def _ema(x: _Series, w: int, n: int) -> _Series:
    """首个非缺值处播种；中途遇缺值断开、之后重新播种。"""
    out: _Series = [None] * n
    alpha = 2 / (w + 1)
    ema: float | None = None
    for i in range(n):
        v = x[i]
        if v is None:
            ema = None
            continue
        ema = v if ema is None else alpha * v + (1 - alpha) * ema
        out[i] = ema
    return out


def _rolling(name: str, x: _Series, w: int, n: int) -> _Series:
    """O(n) 滚动算子。窗口含当日；窗口不足或窗口内有缺值 → None。"""
    # 缺值前缀计数：窗口 [i-w+1, i] 有效 iff i >= w-1 且窗口内无 None
    none_pref = [0] * (n + 1)
    for i in range(n):
        none_pref[i + 1] = none_pref[i] + (x[i] is None)

    def valid(i: int) -> bool:
        return i >= w - 1 and none_pref[i + 1] == none_pref[i - w + 1]

    out: _Series = [None] * n
    if name in ("sum", "ma", "std", "count"):
        # count 的输入是布尔序列，True 记 1；sum/ma/std 是数值
        pref = [0.0] * (n + 1)
        pref_sq = [0.0] * (n + 1) if name == "std" else None
        for i in range(n):
            v = 0.0 if x[i] is None else float(x[i])
            pref[i + 1] = pref[i] + v
            if pref_sq is not None:
                pref_sq[i + 1] = pref_sq[i] + v * v
        for i in range(n):
            if not valid(i):
                continue
            total = pref[i + 1] - pref[i - w + 1]
            if name in ("sum", "count"):
                out[i] = total
            elif name == "ma":
                out[i] = total / w
            else:  # std（总体标准差）
                sq = pref_sq[i + 1] - pref_sq[i - w + 1]  # type: ignore[index]
                out[i] = math.sqrt(std_var) if (std_var := max(sq / w - (total / w) ** 2, 0.0)) >= 0 else None
        return out
    if name in ("max", "min"):
        # 单调队列；缺值窗口由 valid() 屏蔽，队列里用哨兵替代 None
        sentinel = float("-inf") if name == "max" else float("inf")
        better = (lambda a, b: a >= b) if name == "max" else (lambda a, b: a <= b)
        dq: list[int] = []  # 存下标，值单调
        vals = [sentinel if v is None else float(v) for v in x]
        for i in range(n):
            while dq and better(vals[i], vals[dq[-1]]):
                dq.pop()
            dq.append(i)
            if dq[0] <= i - w:
                dq.pop(0)
            if valid(i):
                out[i] = vals[dq[0]]
        return out
    # all / any：布尔滚动，基于 True 计数
    true_pref = [0] * (n + 1)
    for i in range(n):
        true_pref[i + 1] = true_pref[i] + (x[i] is True)
    for i in range(n):
        if not valid(i):
            continue
        cnt = true_pref[i + 1] - true_pref[i - w + 1]
        out[i] = cnt == w if name == "all" else cnt > 0
    return out
