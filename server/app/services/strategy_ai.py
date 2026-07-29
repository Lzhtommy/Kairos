"""Natural-language → strategy DSL.

Two backends, selected by settings.ai_provider ("auto" | "rule" | "deepseek"):
- rule: deterministic keyword parser — no network / API key needed (MVP default).
- deepseek: DeepSeek chat API (mainland-reachable) with the factor whitelist +
  JSON output mode. Chosen over Anthropic because the production ECS sits in
  mainland China, where the Anthropic API is geo-blocked.

Both return {"dsl", "explanation", "code"}. The DSL is always validated before use.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.core.config import settings
from app.services.dsl import FACTORS, validate_dsl

# ---- factor / operator lexicons (Chinese) -------------------------------------------------

_FACTOR_KEYWORDS: list[tuple[str, str]] = [
    ("市盈率", "pe"), ("pe", "pe"), ("PE", "pe"),
    ("市净率", "pb"), ("pb", "pb"), ("PB", "pb"),
    ("净资产收益率", "roe"), ("roe", "roe"), ("ROE", "roe"),
    ("换手率", "turnover_rate"),
    ("成交额", "turnover"), ("成交量", "turnover"),
    ("总市值", "market_cap"), ("市值", "market_cap"),
    ("涨跌幅", "change_pct"), ("涨幅", "change_pct"),
    ("股息率", "dividend_yield"), ("股息", "dividend_yield"), ("分红", "dividend_yield"),
    ("股价", "price"), ("价格", "price"), ("最新价", "price"),
]

_GTE = ["不低于", "不少于", "大于等于", "高于", "大于", "超过", "以上", "多于"]
_LTE = ["不高于", "不超过", "小于等于", "低于", "小于", "以下", "少于"]

# 口语行业词 → 申万二级行业名（stock_info.industry 的实际取值，来自腾讯板块分类）。
# 规则解析兜底用；DeepSeek 路径直接在 prompt 里给出全量行业列表，由模型自己映射。
_INDUSTRY_ALIASES: dict[str, list[str]] = {
    "白酒": ["白酒"],
    "银行": ["国有大型银行", "股份制银行", "城商行", "农商行"],
    "新能源": ["电池", "光伏设备", "风电设备", "能源金属"],
    "半导体": ["半导体"],
    "医药": ["化学制药", "生物制品", "中药", "医药商业", "医疗服务"],
    "军工": ["航天装备", "航空装备", "地面兵装", "军工电子", "航海装备"],
    "汽车": ["乘用车", "商用车", "汽车零部件", "汽车服务"],
    "家电": ["白色家电", "黑色家电", "小家电", "厨卫电器", "家电零部件"],
    "食品饮料": ["食品加工", "休闲食品", "饮料乳品", "调味发酵品", "非白酒"],
    "券商": ["证券"],
    "证券": ["证券"],
    "保险": ["保险"],
    "房地产": ["房地产开发", "房地产服务"],
    "地产": ["房地产开发", "房地产服务"],
}


def _find_factor(clause: str) -> str | None:
    for kw, factor in _FACTOR_KEYWORDS:
        if kw in clause:
            return factor
    return None


def _find_number(clause: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)", clause)
    return float(m.group(1)) if m else None


def _parse_clause(clause: str) -> dict[str, Any] | None:
    # industry match
    for kw, boards in _INDUSTRY_ALIASES.items():
        if kw in clause:
            if len(boards) == 1:
                return {"factor": "industry", "op": "eq", "value": boards[0]}
            return {"factor": "industry", "op": "in", "value": boards}

    factor = _find_factor(clause)
    if factor is None:
        return None

    # industry-median reference (e.g. "PE 低于行业中位数")
    if "行业中位数" in clause or "行业中值" in clause:
        op = "lt"
        if any(k in clause for k in _GTE):
            op = "gt"
        return {"factor": factor, "op": op, "ref": "industry_median"}

    num = _find_number(clause)
    if num is None:
        return None

    is_pct = "%" in clause or "％" in clause
    if factor == "dividend_yield" and is_pct:
        num = num / 100.0  # dividend stored as fraction

    if any(k in clause for k in _GTE):
        op = "gte"
    elif any(k in clause for k in _LTE):
        op = "lte"
    else:
        op = "gte"
    return {"factor": factor, "op": op, "value": num}


def _split_clauses(text: str) -> list[str]:
    return [c for c in re.split(r"[，,。；;、\n]|并且|而且|且|同时", text) if c.strip()]


def rule_based(text: str) -> dict[str, Any]:
    filters = []
    for clause in _split_clauses(text):
        parsed = _parse_clause(clause)
        if parsed:
            filters.append(parsed)

    if not filters:
        # sensible default so the feature always produces something usable
        filters = [
            {"factor": "pe", "op": "lte", "value": 30},
            {"factor": "roe", "op": "gte", "value": 10},
        ]

    dsl = {
        "universe": {"exclude": ["ST", "停牌"], "market": ["SH", "SZ"]},
        "filters": filters,
        "rebalance": "monthly_first_trading_day",
        "cost": {"side": "both", "rate": 0.0005},
    }
    return dsl


def _render_code(dsl: dict[str, Any]) -> str:
    lines = ["def screen(stock):", '    """由自然语言描述生成的选股策略"""', "    return ("]
    parts = []
    for f in dsl["filters"]:
        label = FACTORS[f["factor"]][0]
        if f["factor"] == "industry":
            if f["op"] == "in":
                opts = ", ".join(f'"{v}"' for v in f["value"])
                parts.append(f"stock.industry in ({opts})  # {label}")
            else:
                parts.append(f'stock.industry == "{f["value"]}"  # {label}')
        elif "ref" in f:
            parts.append(f'stock.{f["factor"]} {_op_sym(f["op"])} industry_median(stock)  # {label}')
        elif f["op"] == "between":
            parts.append(f'{f["min"]} <= stock.{f["factor"]} <= {f["max"]}  # {label}')
        else:
            parts.append(f'stock.{f["factor"]} {_op_sym(f["op"])} {f["value"]}  # {label}')
    lines.append("        " + "\n        and ".join(parts))
    lines.append("    )")
    lines.append("")
    lines.append(f"# 调仓频率: {dsl['rebalance']}")
    lines.append(f"# 费率: 双边 {dsl['cost']['rate'] * 100:.3f}%")
    return "\n".join(lines)


def _op_sym(op: str) -> str:
    return {"lt": "<", "lte": "<=", "gt": ">", "gte": ">=", "eq": "=="}.get(op, "==")


def _render_explanation(dsl: dict[str, Any]) -> str:
    descs = []
    for f in dsl["filters"]:
        label = FACTORS[f["factor"]][0]
        if f["factor"] == "industry":
            names = "、".join(f["value"]) if f["op"] == "in" else f["value"]
            descs.append(f"限定行业为「{names}」")
        elif "ref" in f:
            descs.append(f"{label} {'高于' if f['op'].startswith('g') else '低于'}行业中位数")
        elif f["op"] == "between":
            descs.append(f"{label} 介于 {f['min']}~{f['max']}")
        else:
            word = "不低于" if f["op"].startswith("g") else "不高于"
            descs.append(f"{label} {word} {f['value']}")
    return "根据你的描述，我生成了以下选股逻辑：" + "；".join(descs) + "。代码见右侧面板，可点击「运行回测」查看历史表现。"


def _deepseek(text: str, industries: list[str] | None = None) -> dict[str, Any] | None:
    if not settings.deepseek_api_key:
        return None
    try:
        factor_doc = "\n".join(f"- {k}: {v[0]} ({v[1]})" for k, v in FACTORS.items())
        industry_doc = (
            "industry 的合法取值（申万二级行业名，必须精确使用，"
            "口语行业词映射到一个或多个取值，如\"银行股\" → in [\"国有大型银行\",\"股份制银行\",\"城商行\",\"农商行\"]）：\n"
            + "、".join(industries) + "\n"
            if industries
            else ""
        )
        prompt = (
            "你是 A 股量化选股助手。把用户需求转成选股 DSL，"
            "以 JSON 输出：{\"filters\": [...], \"explanation\": \"一句话解释\"}。\n"
            "每个 filter 形如 {\"factor\": ..., \"op\": ..., \"value\": ...}，"
            "between 用 min/max 代替 value，行业中位数比较用 {\"factor\": ..., \"op\": ..., \"ref\": \"industry_median\"}。\n"
            "只能使用以下白名单因子：\n"
            f"{factor_doc}\n"
            "算子: 数值型 lt/lte/gt/gte/eq/between，类别型(industry) eq/in。\n"
            f"{industry_doc}"
            "注意单位：市值/成交额单位为亿，换手率/涨跌幅/ROE 为百分数数值，股息率为小数(3% → 0.03)。\n"
            f"用户需求：{text}"
        )
        r = httpx.post(
            f"{settings.deepseek_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
            json={
                "model": settings.deepseek_model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "max_tokens": 1024,
                "temperature": 0,
            },
            timeout=30,
        )
        r.raise_for_status()
        payload = json.loads(r.json()["choices"][0]["message"]["content"])
        dsl = {
            "universe": {"exclude": ["ST", "停牌"], "market": ["SH", "SZ"]},
            "filters": payload["filters"],
            "rebalance": "monthly_first_trading_day",
            "cost": {"side": "both", "rate": 0.0005},
        }
        dsl = validate_dsl(dsl)
        return {"dsl": dsl, "explanation": payload.get("explanation", "")}
    except Exception:  # noqa: BLE001 — any failure → fall back to rule-based
        return None


def generate(text: str, industries: list[str] | None = None) -> dict[str, Any]:
    """NL → {dsl, explanation, code}. Always returns a valid, validated DSL."""
    use = settings.ai_provider.lower()
    result = None
    if use in ("auto", "deepseek"):
        result = _deepseek(text, industries)

    if result is None:
        dsl = validate_dsl(rule_based(text))
        explanation = _render_explanation(dsl)
    else:
        dsl = result["dsl"]
        explanation = result["explanation"] or _render_explanation(dsl)

    return {"dsl": dsl, "explanation": explanation, "code": _render_code(dsl)}
