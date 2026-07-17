"""Strategy DSL: factor whitelist, validation, and execution.

The DSL is the safe, machine-checkable representation of a screening strategy
(see ARCHITECTURE.md §6.2). We never `eval` user/AI code — filters are
interpreted against factor values only.
"""

from __future__ import annotations

from statistics import median
from typing import Any

# factor key -> (human label, kind)  kind: "num" | "cat"
FACTORS: dict[str, tuple[str, str]] = {
    "pe": ("市盈率", "num"),
    "pb": ("市净率", "num"),
    "roe": ("净资产收益率 ROE(%)", "num"),
    "turnover_rate": ("换手率(%)", "num"),
    "turnover": ("成交额(亿)", "num"),
    "market_cap": ("总市值(亿)", "num"),
    "change_pct": ("涨跌幅(%)", "num"),
    "price": ("最新价", "num"),
    "dividend_yield": ("股息率", "num"),
    "industry": ("行业", "cat"),
}

NUM_OPS = {"lt", "lte", "gt", "gte", "eq", "between"}
CAT_OPS = {"eq", "in"}
REFS = {"industry_median"}


class DSLError(ValueError):
    pass


def validate_dsl(dsl: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a DSL dict. Raises DSLError on any illegal part."""
    if not isinstance(dsl, dict):
        raise DSLError("DSL 必须是对象")

    filters = dsl.get("filters", [])
    if not isinstance(filters, list) or not filters:
        raise DSLError("filters 不能为空")

    norm_filters = []
    for i, f in enumerate(filters):
        if not isinstance(f, dict):
            raise DSLError(f"filters[{i}] 必须是对象")
        factor = f.get("factor")
        op = f.get("op")
        if factor not in FACTORS:
            raise DSLError(f"未知因子: {factor}（不在白名单内）")
        _label, kind = FACTORS[factor]
        allowed = NUM_OPS if kind == "num" else CAT_OPS
        if op not in allowed:
            raise DSLError(f"因子 {factor} 不支持算子 {op}")

        entry: dict[str, Any] = {"factor": factor, "op": op}
        if "ref" in f and f["ref"] is not None:
            if f["ref"] not in REFS:
                raise DSLError(f"未知引用值: {f['ref']}")
            if kind != "num":
                raise DSLError(f"因子 {factor} 不支持引用值")
            entry["ref"] = f["ref"]
        elif op == "between":
            lo, hi = f.get("min"), f.get("max")
            if not _is_num(lo) or not _is_num(hi):
                raise DSLError(f"filters[{i}] between 需要合法的 min/max")
            entry["min"], entry["max"] = float(lo), float(hi)
        elif op == "in":
            vals = f.get("value")
            if not isinstance(vals, list) or not vals:
                raise DSLError(f"filters[{i}] in 需要非空数组")
            entry["value"] = [str(v) for v in vals]
        else:
            val = f.get("value")
            if kind == "num":
                if not _is_num(val):
                    raise DSLError(f"filters[{i}] 需要数值 value")
                entry["value"] = float(val)
            else:
                if not isinstance(val, str) or not val:
                    raise DSLError(f"filters[{i}] 需要字符串 value")
                entry["value"] = val
        norm_filters.append(entry)

    universe = dsl.get("universe", {}) or {}
    cost = dsl.get("cost", {}) or {}
    return {
        "universe": {
            "exclude": list(universe.get("exclude", ["ST", "停牌"])),
            "market": list(universe.get("market", ["SH", "SZ"])),
        },
        "filters": norm_filters,
        "rebalance": dsl.get("rebalance", "monthly_first_trading_day"),
        "cost": {
            "side": cost.get("side", "both"),
            "rate": float(cost.get("rate", 0.0005)),
        },
    }


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _cmp(value: float, op: str, target: float) -> bool:
    if op == "lt":
        return value < target
    if op == "lte":
        return value <= target
    if op == "gt":
        return value > target
    if op == "gte":
        return value >= target
    if op == "eq":
        return value == target
    return False


def execute(dsl: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the subset of `rows` matching the DSL.

    Each row must carry the factor keys it references plus 'industry'.
    """
    dsl = validate_dsl(dsl)

    # market universe filter
    markets = set(dsl["universe"]["market"])
    rows = [r for r in rows if r.get("market") in markets]

    # precompute industry medians for any ref-based numeric filter
    medians: dict[str, dict[str, float]] = {}
    for f in dsl["filters"]:
        if f.get("ref") == "industry_median":
            by_ind: dict[str, list[float]] = {}
            for r in rows:
                v = r.get(f["factor"])
                if _is_num(v):
                    by_ind.setdefault(r.get("industry", "—"), []).append(v)
            medians[f["factor"]] = {k: median(v) for k, v in by_ind.items() if v}

    out = []
    for r in rows:
        if all(_match(f, r, medians) for f in dsl["filters"]):
            out.append(r)
    return out


def _match(f: dict[str, Any], row: dict[str, Any], medians: dict[str, dict[str, float]]) -> bool:
    factor = f["factor"]
    op = f["op"]
    val = row.get(factor)

    if factor == "industry":
        if op == "eq":
            return val == f["value"]
        if op == "in":
            return val in f["value"]
        return False

    if not _is_num(val):
        return False  # e.g. PE is None (亏损) never passes a numeric filter

    if op == "between":
        return f["min"] <= val <= f["max"]
    if "ref" in f:
        target = medians.get(factor, {}).get(row.get("industry", "—"))
        if target is None:
            return False
        return _cmp(val, op, target)
    return _cmp(val, op, f["value"])
