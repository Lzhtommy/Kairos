"""Strategy DSL: factor whitelist, validation, and execution.

The DSL is the safe, machine-checkable representation of a screening strategy
(see ARCHITECTURE.md §6.2). We never `eval` user/AI code — filters are
interpreted against factor values only.
"""

from __future__ import annotations

from statistics import median
from typing import Any

from app.services.technical import INT_PARAMS as TECH_INT_PARAMS
from app.services.technical import SPECS as TECH_SPECS

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

    filters = dsl.get("filters", []) or []
    if not isinstance(filters, list):
        raise DSLError("filters 必须是数组")

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

    norm_tech = _validate_technical(dsl.get("technical", []) or [])
    if not norm_filters and not norm_tech:
        raise DSLError("filters 与 technical 不能同时为空")

    universe = dsl.get("universe", {}) or {}
    cost = dsl.get("cost", {}) or {}
    return {
        "universe": {
            "exclude": list(universe.get("exclude", ["ST", "停牌"])),
            "market": list(universe.get("market", ["SH", "SZ"])),
        },
        "filters": norm_filters,
        "technical": norm_tech,
        "rebalance": dsl.get("rebalance", "monthly_first_trading_day"),
        "cost": {
            "side": cost.get("side", "both"),
            "rate": float(cost.get("rate", 0.0005)),
        },
    }


def _validate_technical(entries: Any) -> list[dict[str, Any]]:
    """校验 K 线技术条件：类型走白名单，数值参数夹到 SPECS 的安全范围。"""
    if not isinstance(entries, list):
        raise DSLError("technical 必须是数组")
    out: list[dict[str, Any]] = []
    for i, t in enumerate(entries):
        if not isinstance(t, dict):
            raise DSLError(f"technical[{i}] 必须是对象")
        typ = t.get("type")
        if typ not in TECH_SPECS:
            raise DSLError(f"未知技术条件类型: {typ}（可用: {', '.join(TECH_SPECS)}）")
        entry: dict[str, Any] = {"type": typ}
        for param, (default, lo, hi) in TECH_SPECS[typ].items():
            raw = t.get(param, default)
            if not _is_num(raw):
                raise DSLError(f"technical[{i}].{param} 需要数值")
            val = min(max(float(raw), lo), hi)
            entry[param] = int(val) if param in TECH_INT_PARAMS else val
        if typ == "ma_rising":
            ws = t.get("windows", [3, 7])
            if not isinstance(ws, list) or not ws or not all(_is_num(w) for w in ws):
                raise DSLError(f"technical[{i}].windows 需要非空数值数组")
            entry["windows"] = sorted({int(min(max(w, 1), 120)) for w in ws})
        if typ == "ma_cross":
            direction = t.get("direction", "golden")
            if direction not in ("golden", "death"):
                raise DSLError(f"technical[{i}].direction 只能是 golden 或 death")
            entry["direction"] = direction
            if entry["fast"] >= entry["slow"]:
                raise DSLError(f"technical[{i}] 要求 fast < slow")
        if typ == "ma_distance" and entry["min_pct"] > entry["max_pct"]:
            raise DSLError(f"technical[{i}] 要求 min_pct ≤ max_pct")
        out.append(entry)
    return out


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


def apply_universe(dsl: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """universe 过滤：市场范围 + ST 剔除（exclude 里带 "ST" 时生效）。"""
    markets = set(dsl["universe"]["market"])
    rows = [r for r in rows if r.get("market") in markets]
    if any("ST" in e for e in dsl["universe"]["exclude"]):
        rows = [r for r in rows if "ST" not in r.get("name", "")]
    return rows


def execute(dsl: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the subset of `rows` matching the DSL.

    Each row must carry the factor keys it references plus 'industry'.
    """
    dsl = validate_dsl(dsl)
    rows = apply_universe(dsl, rows)

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
