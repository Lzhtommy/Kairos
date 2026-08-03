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
from app.services.dsl import BOARDS, FACTORS, validate_dsl

# ---- factor / operator lexicons (Chinese) -------------------------------------------------

_FACTOR_KEYWORDS: list[tuple[str, str]] = [
    ("市盈率", "pe"), ("pe", "pe"), ("PE", "pe"),
    ("市净率", "pb"), ("pb", "pb"), ("PB", "pb"),
    ("净资产收益率", "roe"), ("roe", "roe"), ("ROE", "roe"),
    ("换手率", "turnover_rate"),
    ("成交额", "turnover"), ("成交量", "turnover"),
    ("总市值", "market_cap"), ("市值", "market_cap"),
    # 更长的关键词放前面：按序匹配，"最高价涨跌幅"不能落进"涨跌幅"
    ("最高价涨跌幅", "high_change_pct"), ("盘中涨幅", "high_change_pct"),
    ("最高涨幅", "high_change_pct"), ("冲高", "high_change_pct"),
    ("最低价涨跌幅", "low_change_pct"), ("盘中跌幅", "low_change_pct"),
    ("最大回落", "low_change_pct"), ("下探", "low_change_pct"),
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


def _parse_technical(text: str) -> list[dict[str, Any]]:
    """技术形态的关键词兜底（主路径是 DeepSeek，按 prompt 生成参数化条件）。"""
    tech: list[dict[str, Any]] = []
    if "死叉" in text:
        tech.append({"type": "ma_cross", "fast": 3, "slow": 7, "direction": "death"})
    elif "金叉" in text:
        tech.append({"type": "ma_cross", "fast": 3, "slow": 7, "direction": "golden"})
    if any(k in text for k in ("双线向上", "均线向上", "均线上翘", "均线同步向上")):
        tech.append({"type": "ma_rising", "windows": [3, 7]})
    if any(k in text for k in ("趋势平滑", "平滑上行", "稳步上行", "长期趋势向上")):
        tech.append({"type": "ma_trend", "window": 60, "lookback": 120,
                     "max_down_days": 10, "min_gain_pct": 1.5})
    return tech


def _parse_board(text: str) -> list[str] | None:
    """板块的正向限定兜底（"只要主板/创业板"）；含否定词的复杂表达交给模型。"""
    if any(neg in text for neg in ("排除", "不要", "剔除", "不含", "去掉")):
        return None
    hits = [b for kw, b in (("主板", "main"), ("创业板", "chinext"),
                            ("科创板", "star"), ("北交所", "bj")) if kw in text]
    return sorted(set(hits)) or None


def rule_based(text: str) -> dict[str, Any]:
    filters = []
    for clause in _split_clauses(text):
        parsed = _parse_clause(clause)
        if parsed:
            filters.append(parsed)
    technical = _parse_technical(text)

    if not filters and not technical:
        # sensible default so the feature always produces something usable
        filters = [
            {"factor": "pe", "op": "lte", "value": 30},
            {"factor": "roe", "op": "gte", "value": 10},
        ]

    universe: dict[str, Any] = {"exclude": ["ST", "停牌"], "market": ["SH", "SZ"]}
    board = _parse_board(text)
    if board:
        universe["board"] = board

    dsl = {
        "universe": universe,
        "filters": filters,
        "technical": technical,
        "rebalance": "monthly_first_trading_day",
        "cost": {"side": "both", "rate": 0.0005},
    }
    return dsl


def _tech_desc(t: dict[str, Any]) -> str:
    if t["type"] == "ma_trend":
        return (
            f"MA{t['window']} 近{t['lookback']}日平滑上行"
            f"（回调≤{t['max_down_days']}天且累计涨幅≥{t['min_gain_pct']}%）"
        )
    if t["type"] == "ma_distance":
        return f"MA{t['fast']} 偏离 MA{t['base']} 在 {t['min_pct']}%~{t['max_pct']}% 之间"
    if t["type"] == "ma_rising":
        names = "、".join(f"MA{w}" for w in t["windows"])
        return f"{names} 同步上翘"
    if t["type"] == "ma_cross":
        word = "死叉" if t.get("direction") == "death" else "金叉"
        return f"MA{t['fast']} 刚{word} MA{t['slow']}（首日）"
    if t["type"] == "vol_surge":
        return f"成交量 ≥ {t['window']}日均量 × {t['ratio']}（放量）"
    if t["type"] == "breakout":
        return f"收盘创 {t['window']} 日新高（突破首日）"
    if t["type"] == "macd_cross":
        word = "死叉" if t.get("direction") == "death" else "金叉"
        return f"MACD({t['fast']},{t['slow']},{t['signal']}) {word}首日"
    if t["type"] == "rsi_range":
        return f"RSI{t['window']} 在 {t['min']}~{t['max']} 区间"
    return t["type"]


_SCOPE_LABEL = {"market": "全市场", "industry": "行业内"}
_CROSS_DESC = {
    "rank_top": ("最高前 {n} 名", "排名前{n}"),
    "rank_bottom": ("最低前 {n} 名", "排名后{n}"),
    "pct_top": ("最高 {n}%", "前{n}%"),
    "pct_bottom": ("最低 {n}%", "后{n}%"),
}


def _cross_desc(f: dict[str, Any]) -> str:
    label = FACTORS[f["factor"]][0]
    n = int(f["value"]) if f["op"].startswith("rank") else f["value"]
    return f"{label} {_SCOPE_LABEL[f['scope']]}{_CROSS_DESC[f['op']][0].format(n=n)}"


def _score_desc(score: dict[str, Any]) -> str:
    parts = "、".join(
        f"{FACTORS[f['factor']][0]}({f['weight'] * 100:.0f}%{'，越小越好' if f['direction'] == 'asc' else ''})"
        for f in score["factors"]
    )
    return f"按 {parts} 综合打分取前 {score['top_n']} 只"


def _tech_call(t: dict[str, Any]) -> str:
    args = ", ".join(f"{k}={v!r}" for k, v in t.items() if k != "type")
    return f"{t['type']}(stock, {args})"


def _render_code(dsl: dict[str, Any]) -> str:
    lines = ["def screen(stock):", '    """由自然语言描述生成的选股策略"""', "    return ("]
    parts = []
    for f in dsl["filters"]:
        label = FACTORS[f["factor"]][0]
        if f["op"] in ("rank_top", "rank_bottom", "pct_top", "pct_bottom"):
            scope = "" if f["scope"] == "market" else ', scope="industry"'
            parts.append(f'{f["op"]}(stock.{f["factor"]}, {f["value"]}{scope})  # {_cross_desc(f)}')
        elif f["factor"] == "industry":
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
    for t in dsl.get("technical", []):
        parts.append(f"{_tech_call(t)}  # {_tech_desc(t)}")
    if not parts:
        parts.append("True  # 无过滤条件，仅按打分选优")
    lines.append("        " + "\n        and ".join(parts))
    lines.append("    )")
    score = dsl.get("score")
    if score:
        lines.append("")
        weights = ", ".join(
            f'("{f["factor"]}", {f["weight"]}, "{f["direction"]}")' for f in score["factors"]
        )
        lines.append(f"# 多因子打分: rank_score([{weights}]) 取前 {score['top_n']} 只")
        lines.append(f"# {_score_desc(score)}")
    lines.append("")
    if dsl["universe"].get("board"):
        names = "、".join(BOARDS[b] for b in dsl["universe"]["board"])
        lines.append(f"# 板块范围: {names}")
    lines.append(f"# 调仓频率: {dsl['rebalance']}")
    lines.append(f"# 费率: 双边 {dsl['cost']['rate'] * 100:.3f}%")
    return "\n".join(lines)


def _op_sym(op: str) -> str:
    return {"lt": "<", "lte": "<=", "gt": ">", "gte": ">=", "eq": "=="}.get(op, "==")


def _render_explanation(dsl: dict[str, Any]) -> str:
    descs = []
    for f in dsl["filters"]:
        label = FACTORS[f["factor"]][0]
        if f["op"] in ("rank_top", "rank_bottom", "pct_top", "pct_bottom"):
            descs.append(_cross_desc(f))
        elif f["factor"] == "industry":
            names = "、".join(f["value"]) if f["op"] == "in" else f["value"]
            descs.append(f"限定行业为「{names}」")
        elif "ref" in f:
            descs.append(f"{label} {'高于' if f['op'].startswith('g') else '低于'}行业中位数")
        elif f["op"] == "between":
            descs.append(f"{label} 介于 {f['min']}~{f['max']}")
        else:
            word = "不低于" if f["op"].startswith("g") else "不高于"
            descs.append(f"{label} {word} {f['value']}")
    for t in dsl.get("technical", []):
        descs.append(_tech_desc(t))
    if dsl.get("score"):
        descs.append(_score_desc(dsl["score"]))
    if dsl["universe"].get("board"):
        descs.insert(0, "限定" + "、".join(BOARDS[b] for b in dsl["universe"]["board"]))
    return "根据你的描述，我生成了以下选股逻辑：" + "；".join(descs) + "。代码见右侧面板，可点击「运行回测」查看历史表现。"


def _spec_doc(industries: list[str] | None = None) -> str:
    """DSL 规格说明（因子白名单 / 算子 / 行业取值 / technical 类型 / 单位），单发与多轮共用。"""
    factor_doc = "\n".join(f"- {k}: {v[0]} ({v[1]})" for k, v in FACTORS.items())
    industry_doc = (
        "industry 的合法取值（申万二级行业名，必须精确使用，"
        "口语行业词映射到一个或多个取值，如\"银行股\" → in [\"国有大型银行\",\"股份制银行\",\"城商行\",\"农商行\"]）：\n"
        + "、".join(industries) + "\n"
        if industries
        else ""
    )
    return (
        "每个 filter 形如 {\"factor\": ..., \"op\": ..., \"value\": ...}，"
        "between 用 min/max 代替 value，行业中位数比较用 {\"factor\": ..., \"op\": ..., \"ref\": \"industry_median\"}。\n"
        "只能使用以下白名单因子：\n"
        f"{factor_doc}\n"
        "算子: 数值型 lt/lte/gt/gte/eq/between，类别型(industry) eq/in。\n"
        "截面算子（数值因子）：rank_top/rank_bottom（value=名次 N）、pct_top/pct_bottom"
        "（value=百分比，如最低 10% → {\"factor\":\"pe\",\"op\":\"pct_bottom\",\"value\":10}），"
        "可加 \"scope\":\"industry\" 表示行业内排名（默认 market 全市场）。\n"
        f"{industry_doc}"
        "多因子选优（\"打分取前 N\"类需求）用顶层 score 节（与 filters 并列，可同时用）：\n"
        "{\"score\":{\"factors\":[{\"factor\":\"roe\",\"weight\":0.6,\"direction\":\"desc\"},"
        "{\"factor\":\"pe\",\"weight\":0.4,\"direction\":\"asc\"}],\"top_n\":30}}"
        " → 各因子截面排名归一后加权求和取前 top_n；direction: desc=越大越好, asc=越小越好。\n"
        "涉及均线/K线/量能形态时用 technical 数组，只有以下 8 种类型（参数可调）：\n"
        "- {\"type\":\"ma_trend\",\"window\":60,\"lookback\":120,\"max_down_days\":10,\"min_gain_pct\":1.5}"
        " → MA{window} 在最近 lookback 个交易日平滑上行：逐日滚动算 MA，"
        "下行天数≤max_down_days 且 MA 首尾累计涨幅≥min_gain_pct(%)\n"
        "- {\"type\":\"ma_distance\",\"fast\":3,\"base\":60,\"min_pct\":-8,\"max_pct\":12}"
        " → MA{fast} 相对 MA{base} 的偏离百分比在 [min_pct, max_pct] 区间内\n"
        "- {\"type\":\"ma_rising\",\"windows\":[3,7]}"
        " → windows 里每条均线今日值都高于昨日值（同步上翘）\n"
        "- {\"type\":\"ma_cross\",\"fast\":3,\"slow\":7,\"direction\":\"golden\"}"
        " → MA{fast} 今日刚上穿 MA{slow}（金叉首日；death 为死叉）\n"
        "- {\"type\":\"vol_surge\",\"window\":20,\"ratio\":2.0}"
        " → 当日成交量 ≥ 前 window 日均量 × ratio（放量）\n"
        "- {\"type\":\"breakout\",\"window\":60}"
        " → 收盘价创前 window 日新高（突破首日）\n"
        "- {\"type\":\"macd_cross\",\"fast\":12,\"slow\":26,\"signal\":9,\"direction\":\"golden\"}"
        " → MACD DIF 上穿 DEA（金叉首日；death 为死叉）\n"
        "- {\"type\":\"rsi_range\",\"window\":14,\"min\":0,\"max\":30}"
        " → RSI 落于 [min, max]（如超卖 [0,30]、超买 [70,100]）\n"
        "change_pct 是收盘（盘中为最新价）相对前收的涨跌幅；high_change_pct/low_change_pct "
        "是当日最高/最低价相对前收的涨跌幅（\"盘中一度涨超 5%\" → high_change_pct gte 5，"
        "\"盘中最多跌 3% 以内\" → low_change_pct gte -3）。\n"
        "注意单位：市值/成交额单位为亿，换手率/涨跌幅/ROE 为百分数数值，股息率为小数(3% → 0.03)。\n"
        "涉及板块范围时输出 \"board\": [...]，可选值 main(主板)/chinext(创业板)/star(科创板)/bj(北交所)；"
        "如\"排除创业板和科创板\" → [\"main\",\"bj\"]，\"只要主板\" → [\"main\"]；不限板块则省略该字段。\n"
    )


def _build_dsl(payload: dict[str, Any]) -> dict[str, Any]:
    """模型输出的 {filters, technical, score, board} → 完整 DSL（带 universe/调仓/费率默认值）并校验。"""
    universe: dict[str, Any] = {"exclude": ["ST", "停牌"], "market": ["SH", "SZ"]}
    if payload.get("board"):
        universe["board"] = payload["board"]
    return validate_dsl(
        {
            "universe": universe,
            "filters": payload.get("filters", []),
            "technical": payload.get("technical", []),
            "score": payload.get("score"),
            "rebalance": "monthly_first_trading_day",
            "cost": {"side": "both", "rate": 0.0005},
        }
    )


def _deepseek(text: str, industries: list[str] | None = None) -> dict[str, Any] | None:
    if not settings.deepseek_api_key:
        return None
    try:
        prompt = (
            "你是 A 股量化选股助手。把用户需求转成选股 DSL，"
            "以 JSON 输出：{\"filters\": [...], \"technical\": [...], \"score\": {...}|null, "
            "\"explanation\": \"一句话解释\"}（三者不能全为空）。\n"
            + _spec_doc(industries)
            + f"用户需求：{text}"
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
        dsl = _build_dsl(payload)
        return {"dsl": dsl, "explanation": payload.get("explanation", "")}
    except Exception:  # noqa: BLE001 — any failure → fall back to rule-based
        return None


_MARK_START, _MARK_END = "<DSL>", "</DSL>"


async def chat_stream(
    text: str,
    history: list[dict[str, str]] | None = None,
    current_dsl: dict[str, Any] | None = None,
    industries: list[str] | None = None,
    last_backtest: dict[str, Any] | None = None,
):
    """真流式多轮对话。逐个 yield {"type": "text", "delta": ...}，
    最后 yield {"type": "done", ["dsl": ..., "code": ...]}（纯闲聊时无 dsl/code）。

    协议：模型自由输出中文回复直接透传；本轮涉及策略生成/修改时，模型在结尾追加
    <DSL>{"filters": [...], "technical": [...]}</DSL>，该块不透传，校验后放进 done 事件。
    任何网络/解析异常向上抛，由路由层降级到规则解析。
    """
    sys_prompt = (
        "你是 Kairos 平台的 A 股选股策略助手，与用户多轮对话，帮他们把想法变成可回测的选股策略。\n"
        "回复规则：\n"
        "1. 始终用简短自然的中文对话。问候、闲聊或与选股无关的问题直接回答即可，不要生成策略。\n"
        "2. 当用户提出或修改选股条件时：先用一两句话说明策略逻辑，"
        "然后另起一行输出 <DSL>{\"name\": \"策略标题\", \"filters\": [...], \"technical\": [...],"
        " \"score\": {...} 或 null, \"board\": [...] 或省略}</DSL>。"
        "name 是给这个策略起的简短标题（中文，不超过 12 字，概括策略思路，如\"低估值高分红银行\"）。"
        "<DSL> 块内是严格 JSON；除该块外不要输出任何代码块或 JSON。\n"
        "3. 修改类请求（如\"把 PE 收紧到 20\"）要在【当前策略】基础上输出完整的新 DSL，而不是只给改动部分。\n"
        + _spec_doc(industries)
        + "【当前策略】："
        + (json.dumps(current_dsl, ensure_ascii=False) if current_dsl else "（无）")
        + (
            "\n【最近回测】：" + json.dumps(last_backtest, ensure_ascii=False)
            + "\n用户询问回测结果或要求诊断/优化时：结合上面的指标与最差调仓期/最差交易做归因分析"
            "（如回撤集中在哪段、哪类股票拖累），给出可执行的改进方向；若给出改进后的策略，"
            "按第 2 条规则输出完整新 DSL。"
            if last_backtest
            else ""
        )
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": sys_prompt}]
    for h in (history or [])[-12:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"][:2000]})
    messages.append({"role": "user", "content": text})

    pending = ""  # 已收到但还没透传的文本（留尾巴防止 <DSL> 标记被切成两半）
    dsl_raw: str | None = None  # 进入 <DSL> 块后累积的 JSON 原文
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10)) as client:
        async with client.stream(
            "POST",
            f"{settings.deepseek_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
            json={
                "model": settings.deepseek_model,
                "messages": messages,
                "max_tokens": 1200,
                "temperature": 0.3,
                "stream": True,
            },
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                delta = json.loads(data)["choices"][0]["delta"].get("content") or ""
                if not delta:
                    continue
                if dsl_raw is not None:
                    dsl_raw += delta
                    continue
                pending += delta
                idx = pending.find(_MARK_START)
                if idx != -1:
                    head = pending[:idx].rstrip()
                    if head:
                        yield {"type": "text", "delta": head}
                    dsl_raw = pending[idx + len(_MARK_START):]
                    pending = ""
                elif len(pending) > len(_MARK_START):
                    flush, pending = pending[: -len(_MARK_START)], pending[-len(_MARK_START):]
                    yield {"type": "text", "delta": flush}

    if pending:
        yield {"type": "text", "delta": pending}
    if dsl_raw is not None:
        try:
            end = dsl_raw.find(_MARK_END)
            payload = json.loads(dsl_raw[:end] if end != -1 else dsl_raw)
            dsl = _build_dsl(payload)
            done: dict[str, Any] = {"type": "done", "dsl": dsl, "code": _render_code(dsl)}
            name = payload.get("name")
            if isinstance(name, str) and name.strip():
                done["name"] = name.strip()[:24]
            yield done
            return
        except Exception:  # noqa: BLE001 — 模型产出不合法 DSL，提示用户重试而非中断
            yield {"type": "text", "delta": "\n\n（这组条件我没能生成有效策略，麻烦把条件说得再具体一点）"}
    yield {"type": "done"}


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
