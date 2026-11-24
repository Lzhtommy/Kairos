"""研报撰写：按章调用 DeepSeek 写解读 + 数字 / 禁用词校验 + 拼全文 Markdown。

铁律：LLM 不算数、不造数——每章只给该章相关的 facts（已格式化的显示串），
写完把正文里的数字全部抽出来对照 facts，对不上就带着错误清单重写一次，
仍不通过则保留正文但标 warning（前端提示「待人工核对」）。
没有 DeepSeek key 时整条流水线照跑，各章 md 为空、摘要观察用代码模板生成。
"""

from __future__ import annotations

import json
import logging
import re
import time

import httpx

from app.services import llm
from app.services.research_annual import BLOCK_LABELS, verify_extract
from app.services.research_facts import fmt
from app.services.research_score import AR_IMMATERIAL_PCT, DIMENSION_LABELS
from app.services.research_tracking import build_tracking

logger = logging.getLogger("kairos.research")

DISCLAIMER = "本报告由程序基于公开数据自动生成，仅供研究参考，不构成投资建议。"

# (key, 标题, 指标表对应的评分维度, 该章可用的 fact group)
SECTIONS: list[tuple[str, str, list[str], list[str]]] = [
    ("summary", "摘要", [], []),
    ("overview", "公司概况与主营构成", [], ["segment"]),
    ("profitability", "盈利能力", ["profitability"], ["profitability"]),
    ("growth", "成长性", ["growth"], ["growth", "quarterly"]),
    ("health", "财务健康与现金流", ["health", "cashflow"], ["health", "cashflow"]),
    ("valuation", "估值与股东回报", ["valuation", "shareholder"], ["valuation", "shareholder"]),
    ("peers", "同业对比", [], ["peers"]),
    ("events", "近期重大公告", ["events"], []),
    ("news", "新闻与机构观点", [], ["consensus"]),
    ("risks", "风险提示", [], ["profitability", "growth", "health", "cashflow", "valuation", "shareholder"]),
    ("tracking", "后续跟踪", [], []),
]

# 纯代码生成、不调 LLM 的章节
CODE_ONLY_SECTIONS = {"tracking"}

# 各章「参考指标（不计分）」表：存在才展示；以 * 结尾的按前缀匹配带年份的 fact（见 _info_facts）
SECTION_INFO_FACTS = {
    "profitability": ["gm_cum", "gm_cum_prev", "gm_cum_change", "nm_cum", "nm_cum_prev", "nm_cum_change"],
    "valuation": ["inst_eps_fc_*", "fwd_pe_*", "inst_eps_growth_fc_*", "inst_eps_fc_n_*", "inst_eps_fc_min_*",
                  "inst_eps_fc_max_*", "inst_eps_fc_cv_*", "inst_eps_progress", "eps_progress_prev"],
    "health": ["contract_liab", "contract_liab_yoy", "ar_to_revenue"],
    # 本公司 ROE / 市盈率 / 市净率 / 股息率已在对比表的本公司行，这里只放分位与中位数
    "peers": ["roe_pct_industry", "industry_median_roe", "industry_median_pe", "pb_pct_industry",
              "industry_median_pb", "industry_median_dividend_yield",
              "revenue_yoy_pct_industry", "industry_median_revenue_yoy", "industry_revenue_yoy_agg",
              "industry_revenue_yoy_agg_ex_self", "profit_yoy_pct_industry", "industry_median_profit_yoy",
              "industry_net_profit_yoy_agg", "industry_net_profit_yoy_agg_ex_self"],
}

# 各章正文字数：表格已列全数字，正文只做解读；公告 / 新闻没什么可说时宁短勿凑
SECTION_LENGTH = {
    "overview": "200~300", "profitability": "200~300", "growth": "200~350", "health": "200~350",
    "valuation": "200~350", "peers": "150~250", "events": "50~150", "news": "80~200", "risks": "150~300",
}

# 新闻章不再写机构一致预期（估值章已讲）
NEWS_EXCLUDE_PREFIXES = ("inst_eps_fc_", "inst_pe_fc_")

# 自身估值历史太短时禁止的说法（分位只代表近一两年）
SHORT_HISTORY_BANNED = ["自身历史", "历史高位", "历史低位", "历史高点", "历史低点", "历史底部", "历史顶部", "历史最高", "历史最低"]

BANNED = [
    "建议买入", "建议卖出", "建议增持", "建议减持", "建议持有", "目标价", "强烈推荐", "推荐买入",
    "合理价格", "合理价位", "合理股价", "价格区间",
    "值得买入", "逢低买入", "逢高卖出", "抄底", "止损", "加仓", "减仓", "满仓", "清仓",
]

# 元话语：模型把「材料里有什么 / 没有什么」写进正文（金徽酒报告出现过
# 「事实中未提供公司对……的解释，故此处仅陈述数据呈现的现象」）
META_PHRASES = [
    "事实中", "背景材料", "所给材料", "给定的", "未提供公司", "未提供相关", "故此处仅", "仅陈述",
    "需结合公告", "进一步确认",
]

SYSTEM_PROMPT = (
    "你是 A 股基本面研究员，为一份程序化研报撰写某一章的文字解读。规则：\n"
    "1. 只能使用「事实」中给出的数字，原样照抄其显示格式；禁止自行计算差值、比值、平均数，禁止出现事实里没有的数字。\n"
    "2. 不要逐条复述数字，重点解释数字背后的含义与经营的关系。背景材料里有「公司解释（摘自定期报告）」时，"
    "用它说明变化原因并注明出处（如「据 2026 半年报」「据 2025 年报」）；各报告解释的是各自报告期的同比变化"
    "（年报 = 全年，半年报 = 上半年，一季报 / 三季报 = 年初至该季末），只能用来解释对应期间的数字，不要张冠李戴。"
    "没有公司解释的变化只客观陈述现象，不要自行推测原因。\n"
    "3. 正文是写给读者的研报，不要提及你拿到的材料本身：禁止出现「事实中」「背景材料」「未提供解释」"
    "「故此处仅陈述」「需结合公告进一步确认」这类说明材料范围的话。\n"
    "4. 不给任何投资建议：禁止出现买入、卖出、增减持建议、目标价、仓位等表述。\n"
    "5. 中文，客观克制，字数按「本章字数」要求，Markdown 段落（可用少量无序列表），不要写章节标题。"
    "本章上方已有表格列出全部数字，正文只挑支撑判断的关键数字（每段不超过 3 个），不要把表格再念一遍。\n"
    "输出严格 JSON：{\"markdown\": \"正文\"}"
)


# ---------------------------------------------------------------- 校验

# 千分位数字整体匹配（「2,285 个」是 2285，不是 2 和 285）
_NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
# 日期只认「年份 + 明确的月 / 日分隔」：旧写法 \d{4}[年.]\d{1,2} 会把「2023年665.93亿元」的「2023年66」
# 当成日期吃掉、剩下「5.93」误报，也会把「1720.54」当成「1720.5」
_Y = r"(?<![\d.,])(?:19|20)\d{2}"
_DATE_RE = re.compile(
    rf"{_Y}\s*[-~—至到]\s*(?:19|20)\d{{2}}(?![\d.])"
    rf"|{_Y}-\d{{1,2}}(?:-\d{{1,2}})?(?![\d.])"
    rf"|{_Y}/\d{{1,2}}(?:/\d{{1,2}})?(?![\d.])"
    rf"|{_Y}\.\d{{1,2}}\.\d{{1,2}}(?![\d.])"
    rf"|{_Y}\s*年\s*\d{{1,2}}\s*月(?:\s*\d{{1,2}}\s*日)?"
    rf"|{_Y}\s*年(?:度)?"
    r"|[QH][1-4](?!\d)|第[一二三四]季度"
)


def _to_float(m: str) -> float:
    return float(m.replace(",", ""))


def allowed_numbers(facts: dict, extra_texts: list[str]) -> set[float]:
    out: set[float] = set()
    for f in facts.values():
        # 标签里也有数字（「近 3 年」「509 个交易日」「57 家」），同样允许引用
        for m in _NUM_RE.findall(f.get("label") or ""):
            out.add(round(_to_float(m), 2))
        v = f.get("value")
        if v is None:
            continue
        for d in (0, 1, 2):
            out.add(round(abs(v), d))
    for t in extra_texts:
        for m in _NUM_RE.findall(t):
            out.add(round(_to_float(m), 2))
    return out


def find_bad_numbers(md: str, allowed: set[float]) -> list[str]:
    text = _DATE_RE.sub(" ", md)
    bad = []
    for m in _NUM_RE.findall(text):
        x = _to_float(m)
        if x == int(x) and (0 <= x <= 10 or 1990 <= x <= 2100):
            continue  # 计数词、年份
        decimals = len(m.split(".")[1]) if "." in m else 0
        if not any(abs(round(a, decimals) - x) < 1e-9 for a in allowed):
            bad.append(m)
    return bad


def find_banned(md: str, extra: list[str] | None = None) -> list[str]:
    return [w for w in BANNED + (extra or []) if w in md]


def find_meta(md: str) -> list[str]:
    return [w for w in META_PHRASES if w in md]


# ---------------------------------------------------------------- LLM


def llm_enabled() -> bool:
    return llm.enabled()


def _chat_json(messages: list[dict], max_tokens: int = 1500) -> tuple[dict, int]:
    # 网络抖动（SSL EOF / 连接被断）重试两次；JSON 解析失败重试一次
    # （flash 偶发在 JSON 后多吐内容或混入特殊 token）；HTTP 4xx/5xx 直接抛
    bad_json = 0
    for attempt in range(3):
        try:
            return _chat_json_once(messages, max_tokens)
        except httpx.TransportError as exc:
            if attempt == 2:
                raise
            logger.info("LLM transport error (%s), retrying", exc)
            time.sleep(2 * (attempt + 1))
        except json.JSONDecodeError as exc:
            bad_json += 1
            if bad_json > 1 or attempt == 2:
                raise
            logger.info("LLM returned invalid JSON (%s), retrying", exc)
    raise AssertionError("unreachable")


def _chat_json_once(messages: list[dict], max_tokens: int = 1500) -> tuple[dict, int]:
    return llm.chat_json(messages, max_tokens=max_tokens, temperature=0.2, timeout=90)


def _write_validated(
    user_prompt: str, key: str, allowed: set[float], banned: list[str] | None = None
) -> tuple[dict, int, str | None]:
    """调 LLM + 校验，失败带错误清单重写一次。返回 (payload, tokens, warning)。"""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}]
    tokens = 0
    payload: dict = {}
    problems: list[str] = []
    for _attempt in range(2):
        payload, used = _chat_json(messages)
        tokens += used
        text = payload.get("markdown") or "\n".join(payload.get("observations") or [])
        problems = [f"不在事实中的数字：{n}" for n in find_bad_numbers(text, allowed)]
        problems += [f"禁用表述：{w}" for w in find_banned(text, banned)]
        problems += [f"不要提及材料本身：{w}" for w in find_meta(text)]
        if not problems:
            return payload, tokens, None
        messages += [
            {"role": "assistant", "content": json.dumps(payload, ensure_ascii=False)},
            {"role": "user", "content": "校验未通过，请修正后重新输出完整 JSON：\n" + "\n".join(problems)},
        ]
    logger.info("Research section %s failed validation: %s", key, problems)
    return payload, tokens, "部分内容未通过自动校验，待人工核对：" + "；".join(problems[:5])


# ---------------------------------------------------------------- 年报抽取（§10）

EXTRACT_VERSION = "a4"  # 改抽取提示词 / 校验规则时升版本，旧缓存自动失效

EXTRACT_PROMPT = (
    "你是上市公司定期报告信息摘录员。从给定的定期报告片段（年报 / 半年报的「管理层讨论与分析」，"
    "或季报的「主要会计数据、财务指标发生变动的情况、原因」）中摘录信息，只摘录、不评价、不推断。规则：\n"
    "1. 每条都必须带 quote：从原文逐字复制的一段连续文字（20~120 字），不得改写、拼接、省略中间内容。\n"
    "2. drivers：公司对经营变化原因的解释（收入、毛利、费用、现金流、资本开支、存货、利润等）。"
    "topic 取 revenue / margin / expense / cashflow / capex / inventory / balance / other 之一"
    "（balance = 应收、预付、借款、合同负债等资产负债科目的变动原因；利润变动原因归 other）；"
    "claim 用一句话概括（≤60 字），只能使用 quote 里出现的数字，不要把表格里的变动比例等 quote 之外的数字加进来。"
    "「XX 变动原因说明」「注 N：XX 变动主要原因」、季报变动原因表的每一行，各算一条。最多 20 条。\n"
    "3. kpis：公司披露的经营数据（销量、产量、产能、库存、渠道或门店数量、直销占比、在手订单等）。"
    "value 只写数字，照抄原文；unit 写单位；name 写清口径（如「茅台酒销量」）。"
    "也摘录分地区（如省内 / 省外、境内 / 境外）、分销售渠道（经销 / 直销 / 线上）的营业收入及其同比，"
    "name 写清口径（如「省外营业收入」「省外营业收入同比」）。"
    "表格里给出上年同期数或同比增减比例的（如分产品、分渠道收入表的「同比增减」列），"
    "同时摘录一条「XX同比」（value 为原文的增减比例，unit 为 %）。"
    "不要摘录营业收入、净利润等合并报表总额科目。最多 24 条。\n"
    "4. risks：公司自述的经营风险。title 为风险名称（≤12 字），quote 为原文中对该风险的具体描述段落"
    "（风险内容与应对措施），不要引用只列风险名称的总括句（如「一是……二是……」）；"
    "原文只有名称清单、没有逐条展开时，所有风险共用那一句作 quote。最多 8 条。\n"
    "5. 原文没有的类别返回空数组，不要凑数。\n"
    "输出严格 JSON：{\"drivers\": [{\"topic\", \"claim\", \"quote\"}], "
    "\"kpis\": [{\"name\", \"value\", \"unit\", \"quote\"}], \"risks\": [{\"title\", \"quote\"}]}"
)


def extract_periodic(blocks: list[dict], pages: list[tuple[int, str]], doc: str) -> tuple[dict, int]:
    """一份定期报告一次 LLM 调用抽取 drivers / kpis / risks，再由 verify_extract 按原文校验。"""
    text = "\n\n".join(
        f"【{doc}·{BLOCK_LABELS.get(b['key'], b['title'])}】（第 {b['page']} 页起{'，已截断' if b.get('truncated') else ''}）"
        f"\n{b['text']}"
        for b in blocks
    )
    messages = [{"role": "system", "content": EXTRACT_PROMPT}, {"role": "user", "content": text}]
    raw, tokens = _chat_json(messages, max_tokens=4000)
    return verify_extract(raw, pages), tokens


# 各章分配哪些 topic 的公司解释
SECTION_TOPICS = {
    "profitability": {"margin", "expense"},
    "growth": {"revenue", "other"},
    "health": {"cashflow", "capex", "inventory", "balance"},
}


def _info_facts(key: str, facts: dict) -> list[str]:
    """带年份的按（年份，配置顺序）排，同一年的放在一起；其余按配置顺序跟在后面。"""
    pats = SECTION_INFO_FACTS.get(key, [])
    # 前缀后必须正好是年份：「inst_eps_fc_*」不能把 inst_eps_fc_min_2026 也吞进来
    dated = sorted(
        (f[len(p) - 1:], i, f) for i, p in enumerate(pats) if p.endswith("*")
        for f in facts if f.startswith(p[:-1]) and re.fullmatch(r"\d{4}", f[len(p) - 1:])
    )
    return [f for *_, f in dated] + [p for p in pats if not p.endswith("*") and p in facts]


# 每章展示的公司解释按科目排优先级（越靠前越重要），同一科目最多 MAX_PER_SUBJECT 份报告，总数 MAX_CITATIONS。
# 年报 / 半年报的「变动原因说明」动辄二三十条（茅台光财务公司同业存单就十几条），全列只会淹没重点
SECTION_SUBJECTS = {
    "profitability": ("营业成本", "毛利", "销售费用", "管理费用", "研发费用", "税金", "财务费用"),
    "growth": ("营业收入", "营业总收入", "净利润", "销量", "收入"),
    "health": ("经营活动", "合同负债", "预收", "应收账款", "存货", "短期借款", "长期借款", "投资活动", "筹资活动", "货币资金"),
}
MAX_CITATIONS = 5
MAX_PER_SUBJECT = 2
# 只进提示词、不在本章展示的公司解释（风险 / 摘要需要知道「合同负债下降是政策调整」这类原因，免得写反）
PROMPT_ONLY_TOPICS = {
    "summary": {"revenue", "balance", "cashflow", "margin"},
    "risks": {"revenue", "balance", "cashflow", "margin"},
}


def _subject(claim: str, subjects: tuple[str, ...]) -> int:
    return next((i for i, w in enumerate(subjects) if w in claim), len(subjects))


def _pick_citations(cites: list[dict], subjects: tuple[str, ...], limit: int) -> list[dict]:
    """按科目优先级挑 limit 条，同科目最多 MAX_PER_SUBJECT 条（取最新报告期），同一句话只留最新的；输出保持原顺序。"""
    seen_text: set[str] = set()
    per_subject: dict[int, int] = {}
    ranked = sorted(enumerate(cites), key=lambda ic: (_subject(ic[1]["claim"], subjects), ic[0]))
    picked: list[tuple[int, dict]] = []
    for i, c in ranked:
        text = re.sub(r"\s+", "", c["claim"])
        subj = _subject(c["claim"], subjects)
        if text in seen_text or per_subject.get(subj, 0) >= MAX_PER_SUBJECT:
            continue
        seen_text.add(text)
        per_subject[subj] = per_subject.get(subj, 0) + 1
        picked.append((i, c))
        if len(picked) >= limit:
            break
    return [c for _, c in sorted(picked, key=lambda ic: ic[0])]


def _citations(key: str, periodic: dict | None) -> list[dict]:
    topics = SECTION_TOPICS.get(key)
    if not periodic or not topics:
        return []
    cites = [d for d in periodic.get("drivers") or [] if d["topic"] in topics]
    return _pick_citations(cites, SECTION_SUBJECTS.get(key, ()), MAX_CITATIONS)


def _prompt_citations(key: str, periodic: dict | None) -> list[dict]:
    topics = PROMPT_ONLY_TOPICS.get(key)
    if not periodic or not topics:
        return []
    cites = [d for d in periodic.get("drivers") or [] if d["topic"] in topics]
    subjects = SECTION_SUBJECTS["growth"] + SECTION_SUBJECTS["health"] + SECTION_SUBJECTS["profitability"]
    return _pick_citations(cites, subjects, 8)


# ---------------------------------------------------------------- 经营数据矩阵

_YOY_SUFFIX = re.compile(r"(?:同比(?:增减|变动|变化|增长)?(?:率|幅度|比例)?)$")
_AMOUNT_TO_YI = {"亿元": 1.0, "万元": 1e-4, "元": 1e-8}


def _kpi_number(value: str) -> float | None:
    m = re.fullmatch(r"\s*([-+]?[\d,]+(?:\.\d+)?)\s*", value or "")
    return float(m.group(1).replace(",", "")) if m else None


def _kpi_cell(k: dict) -> tuple[str, str]:
    """(显示值, 单位)：金额统一换成亿元（定期报告常用万元 / 元，七八位数字没法读）；其余数字统一千分位。"""
    unit = (k.get("unit") or "").strip()
    raw = (k.get("value") or "").strip()
    x = _kpi_number(raw)
    if x is None:
        return raw, unit
    if unit in _AMOUNT_TO_YI and unit != "亿元":
        return f"{x * _AMOUNT_TO_YI[unit]:.2f}", "亿元"
    decimals = len(raw.split(".")[1]) if "." in raw else 0
    return f"{x:,.{decimals}f}", unit


# 同一指标在不同报告里的叫法（LLM 照抄原文）：归一后才能落到同一行
_KPI_NAME_FIXES = (("主营业务收入", "营业收入"), ("销售收入", "营业收入"), ("报告期", ""))


def _kpi_name(name: str) -> str:
    name = re.sub(r"\s+", "", name or "")
    for a, b in _KPI_NAME_FIXES:
        name = name.replace(a, b)
    return name


def kpi_table(kpis: list[dict]) -> dict | None:
    """经营数据 → 「指标 × 报告期」矩阵：{docs, rows: [{name, cells: {doc: {value, unit, page, yoy?}}}]}。

    「XX同比」条目并进 XX 那一格的 yoy；同一报告同名指标只留第一条。docs 按出现顺序（已是由新到旧）。
    """
    if not kpis:
        return None
    docs: list[str] = []
    rows: dict[str, dict] = {}
    yoys: list[tuple[str, str, dict]] = []
    for k in kpis:
        doc, name = _doc(k), _kpi_name(k.get("name") or "")
        if not name:
            continue
        if doc not in docs:
            docs.append(doc)
        base = _YOY_SUFFIX.sub("", name)
        if base != name and base:
            yoys.append((base, doc, k))
            continue
        row = rows.setdefault(name, {"name": name, "cells": {}})
        if doc not in row["cells"]:
            value, unit = _kpi_cell(k)
            row["cells"][doc] = {"value": value, "unit": unit, "page": k.get("page")}
    for base, doc, k in yoys:
        unit = (k.get("unit") or "").strip()
        value = (k.get("value") or "").strip()
        yoy = value if value.endswith(unit) else f"{value}{unit}"
        cell = rows.get(base, {}).get("cells", {}).get(doc)
        if cell is not None:
            cell.setdefault("yoy", yoy)
        else:  # 只有同比、没有绝对值
            row = rows.setdefault(f"{base}同比", {"name": f"{base}同比", "cells": {}})
            row["cells"].setdefault(doc, {"value": value, "unit": unit or "%", "page": k.get("page")})
    return {"docs": docs, "rows": list(rows.values())} if rows else None


def _kpi_lines(table: dict) -> list[str]:
    lines = []
    for r in table["rows"]:
        parts = []
        for doc in table["docs"]:
            c = r["cells"].get(doc)
            if c:
                yoy = f"（同比 {c['yoy']}）" if c.get("yoy") else ""
                parts.append(f"{doc} {c['value']}{c['unit']}{yoy}")
        lines.append(f"- {r['name']}：" + "；".join(parts))
    return lines


def _doc(item: dict) -> str:
    return item.get("doc") or "年报"  # a1/a2 版报告的条目没有 doc，那时只读年报


def _periodic_block(cites: list[dict]) -> str:
    return "公司解释（摘自定期报告，按报告期由新到旧）：\n" + "\n".join(
        f"- [{_doc(c)} 第 {c['page']} 页] {c['claim']}（原文：「{c['quote']}」）" for c in cites
    )


def _facts_block(facts: dict, groups: list[str], exclude: tuple[str, ...] = ()) -> str:
    lines = []
    for fid, f in facts.items():
        if f.get("group") in groups and not (exclude and fid.startswith(exclude)):
            period = f"（{f['period']}）" if f.get("period") else ""
            lines.append(f"- {f['label']}{period}：{fmt(f)}{_fact_caveat(fid, facts)}")
    return "\n".join(lines) or "（无）"


def _fact_caveat(fid: str, facts: dict) -> str:
    """与评分口径一致的提醒，直接挂在事实后面：模型看到数字时就知道它不重要 / 不能那样解读。"""
    if fid in ("ar_yoy", "ar_minus_rev_growth"):
        ratio = (facts.get("ar_to_revenue") or {}).get("value")
        if ratio is not None and ratio < AR_IMMATERIAL_PCT:
            return f"（应收账款仅占营收 {fmt(facts['ar_to_revenue'])}，基数极小，增速失真，不构成风险，不要作为重点）"
    if fid in ("pe_pct_self", "pb_pct_self") and _short_history(facts):
        return f"（仅覆盖近 {facts[fid].get('days')} 个交易日，不代表长期估值位置，只能写「近 N 个交易日的分位」）"
    return ""


def _score_block(result: dict) -> str:
    lines = []
    for key, d in result["sub_scores"].items():
        s = "数据不足" if d["score"] is None else f"{d['score']}/{d['max']}"
        lines.append(f"- {DIMENSION_LABELS[key]}：{s}")
    return "\n".join(lines)


def _context(key: str, dataset: dict, result: dict) -> tuple[str, list[str]]:
    """每章额外的非数字上下文（文本），以及其中允许出现的数字来源。"""
    if key == "overview":
        p = dataset["profile"]
        text = (
            f"公司名称：{p.get('公司名称')}\n所属行业（证监会）：{p.get('所属行业')}\n"
            f"上市日期：{p.get('上市日期')}\n主营业务：{p.get('主营业务')}\n公司简介：{p.get('机构简介')}"
        )
        return text, [text]
    if key == "events":
        evs = [e for e in dataset.get("events") or [] if e["tag"] != "other"][:20]
        others = [e for e in dataset.get("events") or [] if e["tag"] == "other"][:15]
        text = "已识别的重要公告（代码按关键词分类）：\n" + (
            "\n".join(f"- {e['date']} [{e['label']}] {e['title']}" for e in evs) or "（无）"
        ) + "\n其他公告标题：\n" + ("\n".join(f"- {e['date']} {e['title']}" for e in others) or "（无）") + (
            "\n写作要求：重点解读已识别的重要公告；其他公告只在涉及经营、治理的实质变化（如重大资产交易、"
            "高管变动、会计政策变更影响利润）时提一句。定期报告、股东会决议、权益分派实施、业绩说明会、"
            "董事会决议这类例行公告不要描述，也不要评价「信息披露连续性」。没有值得说的就只写一两句。"
        )
        return text, [text]
    if key == "news":
        news = "\n".join(f"- {n['time'][:10]} {n['title']}（{n['source']}）" for n in dataset.get("news") or [])
        cons = dataset.get("consensus") or {}
        titles = "\n".join(
            f"- {t['date']} {t['org']}《{t['title']}》评级：{t['rating']}" for t in cons.get("titles") or []
        )
        text = (f"近期新闻：\n{news or '（无）'}\n近期机构研报：\n{titles or '（无）'}\n"
                "写作要求：概括机构评级分布与研报关注的主线、新闻里与公司经营直接相关的事项；"
                "机构盈利预测与前瞻估值已在估值章节讨论，这里不要重复；与公司无关的板块资金、个股榜单类新闻不要写。")
        return text, [text]
    if key == "peers":
        p = dataset.get("peers") or {}
        lines = []
        for r in p.get("rows") or []:
            cells = [f"市值 {r['market_cap']:.2f}亿元" if r.get("market_cap") else None,
                     f"市盈率 {r['pe']:.2f}倍" if r.get("pe") and r["pe"] > 0 else "市盈率 亏损",
                     f"市净率 {r['pb']:.2f}倍" if r.get("pb") else None,
                     f"ROE {r['roe']:.2f}%" if r.get("roe") is not None else None,
                     f"股息率 {r['dividend_yield']:.2f}%" if r.get("dividend_yield") is not None else None]
            lines.append(f"- {r['name']}{'（本公司）' if r['self'] else ''}：" + "，".join(c for c in cells if c))
        text = (f"同行业（{p.get('industry')}，共 {p.get('count')} 家）中市值最接近的公司（行情快照 {p.get('as_of')}）：\n"
                + "\n".join(lines) + "\n注意：对比表的 ROE 是 TTM（行情快照），与盈利能力章节的年报 ROE 口径不同，"
                "两者不要混用；对比只说明相对位置，不推断原因。事实里有同行业营收 / 利润增速时，"
                "对比公司与行业中位数、剔除本公司后的行业合计，说明公司跑赢还是跑输行业。")
        return text, [text]
    if key == "risks":
        flags = "\n".join(f"- {f['label']}：{f['detail']}" for f in result["red_flags"]) or "（无）"
        neg = "\n".join(
            f"- {e['date']} [{e['label']}] {e['title']}"
            for e in dataset.get("events") or []
            if e["tag"] in ("reduce", "inquiry", "investigation", "forecast_down", "lawsuit")
        ) or "（无）"
        text = (
            f"各维度得分：\n{_score_block(result)}\n排雷项：\n{flags}\n负面公告：\n{neg}\n"
            "写作要求：前面各章已列出全部数据，本章不要逐项复述指标。只写 3~4 条最主要的风险，"
            "每条用无序列表：风险是什么、由哪个指标体现（最多引用 1 个数字）、什么情况下会恶化。"
            "公司解释说明某项变化来自政策、口径或收款模式调整的，按公司解释表述，不要写成需求恶化；"
            "财务结构稳健的方面不必写。公司自述风险会在本章下方原文列出，不要复述。"
            + ("自身估值分位只覆盖近一两年，不能写成「处于自身历史高位」。" if _short_history(dataset["facts"]) else "")
        )
        return text, [text]
    if key == "valuation":
        facts = dataset["facts"]
        parts = []
        if _short_history(facts):
            days = facts["pe_pct_self"]["days"]
            parts.append(
                f"注意：自身历史分位只覆盖近 {days} 个交易日（不足 2 年），只能说「近 {days} 个交易日的 X% 分位」，"
                "不能代表长期估值区间，禁止写「历史高位 / 历史低位」这类说法。"
            )
        if any(f.startswith("fwd_pe_") for f in facts):
            actual = [f"- {facts[f]['label']}（{facts[f]['period']}）：{fmt(facts[f])}"
                      for f in ("deducted_np_yoy", "revenue_yoy") if f in facts]
            parts.append(
                "最新实际增速：\n" + ("\n".join(actual) or "（无）") + "\n"
                "写作要求：除估值分位外，对照机构一致预期与实际——预测增速与最新实际增速差多少、"
                "预测完成度与去年同期相比快还是慢、机构预测是否分歧较大（区间与离散度）。"
                "只描述「当前估值隐含了怎样的预期、实际进度如何」，不给出合理价格或价格区间。"
            )
        parts.append("股息率是近 12 个月口径，写的时候注明。")
        text = "\n".join(parts)
        return text, [text]
    if key == "growth" and dataset.get("quarters"):
        text = ("写作要求：结合单季序列说明增长节奏——哪个季度是低点、最近一季是否改善、"
                "上年同期基数高低对下一季同比的影响；累计同比与单季同比都要照顾到。")
        return text, [text]
    if key == "profitability" and "gm_cum" in dataset["facts"]:
        text = ("写作要求：年报利润率之外，用最新累计期毛利率 / 归母净利率与上年同期的对比说明利润率的最新方向；"
                "毛利率与净利率走势不一致时，用公司对成本、费用、税金的解释说明原因。")
        return text, [text]
    return "", []


def _short_history(facts: dict) -> bool:
    days = (facts.get("pe_pct_self") or {}).get("days")
    return days is not None and days < 480


# ---------------------------------------------------------------- 主入口


def _fallback_observations(result: dict) -> list[str]:
    dims = [(k, d) for k, d in result["sub_scores"].items() if d["score"] is not None and k != "events"]
    if not dims:
        return []
    ranked = sorted(dims, key=lambda kd: kd[1]["score"] / kd[1]["max"], reverse=True)
    out = [f"{DIMENSION_LABELS[k]}得分 {d['score']}/{d['max']}，为相对优势维度。" for k, d in ranked[:2]]
    k, d = ranked[-1]
    out.append(f"{DIMENSION_LABELS[k]}得分 {d['score']}/{d['max']}，为相对薄弱维度。")
    return out


def write_sections(
    dataset: dict, result: dict, name: str, code: str, progress=None, periodic: dict | None = None
) -> tuple[list[dict], int]:
    """periodic：定期报告深读结果（research_annual.merge_extracts 输出），可为 None。"""
    facts = dataset["facts"]
    sections: list[dict] = []
    tokens = 0
    use_llm = llm_enabled()
    llm_keys = [k for k, *_ in SECTIONS]

    for i, (key, title, metric_keys, groups) in enumerate(SECTIONS, 1):
        if key == "peers" and not dataset.get("peers"):
            continue
        if progress:
            progress(f"撰写 {i}/{len(llm_keys)}：{title}")
        sec: dict = {"key": key, "title": title, "md": "", "metric_keys": metric_keys, "ai": use_llm}
        info = _info_facts(key, facts)
        if info:
            sec["info_facts"] = info
        if key == "peers":
            sec["peers"] = dataset["peers"]
        if key in CODE_ONLY_SECTIONS:
            sec["ai"] = False
            sec["tracking"] = build_tracking(dataset, result, periodic)
            sections.append(sec)
            continue
        if key == "events":
            sec["items"] = [e for e in dataset.get("events") or [] if e["tag"] != "other"][:30]
        if key == "news":
            sec["items"] = dataset.get("news") or []
            sec["reports"] = (dataset.get("consensus") or {}).get("titles") or []
        if key == "overview":
            sec["profile"] = dataset.get("profile") or {}
            sec["segments"] = dataset.get("segments") or []
            sec["kpis"] = (periodic or {}).get("kpis") or []
            table = kpi_table(sec["kpis"])
            if table:
                sec["kpi_table"] = table
        if key == "growth" and dataset.get("quarters"):
            sec["quarters"] = dataset["quarters"]
        if key == "risks":
            sec["company_risks"] = (periodic or {}).get("risks") or []

        context, extra = _context(key, dataset, result)
        cites = _citations(key, periodic)
        if cites:
            sec["citations"] = cites
        cites = cites or _prompt_citations(key, periodic)
        if cites:
            block = _periodic_block(cites)
            context = f"{context}\n{block}" if context else block
            extra = extra + [c["quote"] for c in cites] + [str(c["page"]) for c in cites]
        if key == "overview" and sec.get("kpi_table"):
            block = ("公司披露的经营数据（摘自定期报告，金额已换算为亿元；不同报告期为累计口径，不能直接相减）：\n"
                     + "\n".join(_kpi_lines(sec["kpi_table"])))
            context = f"{context}\n{block}" if context else block
            extra = extra + [block]
        if key == "summary":
            all_facts = _facts_block(facts, ["profitability", "growth", "health", "cashflow", "valuation", "shareholder"])
            flags = "\n".join(f"- {f['label']}：{f['detail']}" for f in result["red_flags"]) or "（无）"
            watch = "\n".join(
                f"- {t['label']}{(' ' + t['value']) if t['value'] else ''}：{t['note']}"
                for t in build_tracking(dataset, result, periodic) if t["kind"] in ("flag", "metric")
            ) or "（无）"
            prompt = (
                f"股票：{name}（{code}）。基本面总分 {result['total_score']}，评级 {result['grade'] or '—'}。\n"
                f"各维度得分：\n{_score_block(result)}\n排雷项：\n{flags}\n程序识别的待跟踪指标：\n{watch}\n"
                f"事实：\n{all_facts}\n" + (f"\n背景材料：\n{context}\n" if context else "") + "\n"
                "任务：写 3 条核心观察，每条 50~90 字，必须引用事实中的数字。"
                "第 1 条用一句话概括这家公司当前基本面最核心的特征与主要矛盾（如「盈利能力顶尖但增长停滞」），"
                "不要只罗列指标；第 2、3 条各写一个接下来最该跟踪的变量：它是什么、当前读数、为什么关键"
                "（公司对该变化有解释的，按解释表述，不要写反）。"
                + ("自身估值历史不足 2 年，不要写「历史高位 / 低位」。" if _short_history(facts) else "")
                + "输出 JSON：{\"observations\": [\"…\", \"…\", \"…\"]}"
            )
            observations = _fallback_observations(result)
            if use_llm:
                try:
                    allowed = allowed_numbers(facts, [flags, watch, _score_block(result), str(result["total_score"])] + extra)
                    payload, used, warning = _write_validated(
                        prompt, key, allowed, SHORT_HISTORY_BANNED if _short_history(facts) else None)
                    tokens += used
                    obs = [str(o) for o in payload.get("observations") or [] if str(o).strip()][:3]
                    if obs:
                        observations = obs
                        sec["warning"] = warning
                except Exception as exc:  # noqa: BLE001 — 单章失败退回模板
                    logger.warning("Research summary LLM failed: %s", exc)
                    sec["ai"] = False
            sec["observations"] = observations
            sec["md"] = "\n".join(f"- {o}" for o in observations)
            sections.append(sec)
            continue

        if use_llm:
            exclude = NEWS_EXCLUDE_PREFIXES if key == "news" else ()
            prompt = (
                f"股票：{name}（{code}）。本章：{title}。本章字数：{SECTION_LENGTH.get(key, '200~350')} 字。\n"
                f"事实：\n{_facts_block(facts, groups, exclude)}\n"
                + (f"\n背景材料：\n{context}\n" if context else "")
                + ("\n本章评分：\n" + "\n".join(
                    f"- {DIMENSION_LABELS[m]}：{result['sub_scores'][m]['score']}/{result['sub_scores'][m]['max']}"
                    for m in metric_keys if result["sub_scores"][m]["score"] is not None
                ) if metric_keys else "")
            )
            try:
                score_texts = [str(result["sub_scores"][m]["score"]) for m in metric_keys] + [
                    str(result["sub_scores"][m]["max"]) for m in metric_keys
                ]
                allowed = allowed_numbers(facts, extra + score_texts)
                banned = SHORT_HISTORY_BANNED if key in ("valuation", "risks") and _short_history(facts) else None
                payload, used, warning = _write_validated(prompt, key, allowed, banned)
                tokens += used
                sec["md"] = _strip_heading(str(payload.get("markdown") or ""))
                sec["warning"] = warning
            except Exception as exc:  # noqa: BLE001 — 单章失败不拖垮整份报告
                logger.warning("Research section %s LLM failed: %s", key, exc)
                sec["ai"] = False
        sections.append(sec)
    return sections, tokens


_LEADING_TITLE = re.compile(r"^\s*(?:#+\s*[^\n]{1,20}|\*\*[^*\n]{1,20}\*\*)\s*\n+")


def _strip_heading(md: str) -> str:
    """模型偶尔不听「不要写章节标题」，在正文前自带一行「**风险提示**」——去掉。"""
    return _LEADING_TITLE.sub("", md.strip(), count=1).strip()


def _cell(v: float | None, suffix: str = "") -> str:
    return "—" if v is None else f"{v:.2f}{suffix}"


SCORING_RULES = (
    "每个指标在「零分点 → 满分点」之间线性插值、两端截断；缺失指标不计分，维度按剩余权重折算；"
    "维度可用满分不足一半记为「数据不足」；整体覆盖率低于 70% 不给评级；命中排雷项评级封顶 C，"
    "其中 ST / 立案调查直接定 D。评分完全由程序按规则计算，AI 只负责文字解读。"
)


def _metric_table(d: dict) -> list[str]:
    """维度指标表：数值、得分、零分点 → 满分点；特殊计分规则（豁免、降权）以脚注列在表下。"""
    out = ["| 指标 | 数值 | 得分 | 零分点 → 满分点 |", "|---|---|---|---|"]
    notes: list[str] = []
    for it in d["items"]:
        if it["points"] is None:
            pts = "缺失，不计分"
        elif it["max"] is None:  # 事件：加减分
            pts = f"{it['points']:+g}"
        else:
            pts = f"{it['points']} / {it['max']}"
        rng = "—" if it.get("zero") is None else f"{it['zero']} → {it['full']}"
        mark = ""
        if it.get("note") and it["max"] is not None:
            if it["note"] not in notes:  # 降权说明三项共用一句，只列一次
                notes.append(it["note"])
            mark = f" [注{notes.index(it['note']) + 1}]"
        out.append(f"| {it['label']}{mark} | {it['display']} | {pts} | {rng} |")
    if notes:
        out += [""] + [f"注{i}：{n}" for i, n in enumerate(notes, 1)]
    if any(it["max"] is None for it in d["items"]):
        out += ["", "事件分以 5 分为基准加减，同类事件只计一次。"]
    return out


def _quarter_table(quarters: list[str], facts: dict) -> list[str]:
    def f(fid: str) -> str:
        return fmt(facts.get(fid)) if fid in facts else "—"

    out = ["**单季度表现**", "", "| 季度 | 营收 | 营收同比 | 归母净利润 | 净利润同比 | 毛利率 |", "|---|---|---|---|---|---|"]
    for d in reversed(quarters):  # 最新在上
        q = f"{d[:4]}Q{('03-31', '06-30', '09-30', '12-31').index(d[5:]) + 1}"
        out.append(f"| {q} | {f(f'q_rev_{d}')} | {f(f'q_rev_yoy_{d}')} | {f(f'q_np_{d}')} | "
                   f"{f(f'q_np_yoy_{d}')} | {f(f'q_gm_{d}')} |")
    out += ["", "单季值由累计值差分得到（新浪财经利润表）。"]
    return out


def _kpi_markdown(table: dict) -> list[str]:
    docs = table["docs"]
    out = ["**经营数据（摘自定期报告，各期为累计口径）**", "",
           "| 项目 | " + " | ".join(docs) + " |", "|---|" + "---|" * len(docs)]
    for r in table["rows"]:
        cells = []
        for doc in docs:
            c = r["cells"].get(doc)
            if not c:
                cells.append("—")
                continue
            yoy = f"（同比 {c['yoy']}）" if c.get("yoy") else ""
            cells.append(f"{c['value']} {c['unit']}{yoy}".strip())
        out.append(f"| {r['name']} | " + " | ".join(cells) + " |")
    pages = sorted({(doc, c["page"]) for r in table["rows"] for doc, c in r["cells"].items() if c.get("page")})
    by_doc: dict[str, list[int]] = {}
    for doc, page in pages:
        by_doc.setdefault(doc, []).append(page)
    out += ["", "出处：" + "；".join(f"{doc} 第 {'、'.join(map(str, ps))} 页" for doc, ps in by_doc.items())]
    return out


def render_markdown(report: dict) -> str:
    """全文 Markdown（导出用）。report 含 name/code/industry/data_as_of/total_score/grade/
    coverage/sub_scores/red_flags/sections/sources/scoring_version/facts。"""
    facts = report.get("facts") or {}
    out = [
        f"# {report['name']}（{report['code']}）基本面研究报告",
        "",
        f"> {DISCLAIMER}",
        "",
        f"行业：{report['industry']} · 报告期：{report['data_as_of'] or '—'} · 规则版本：{report['scoring_version']}",
        "",
        f"**基本面评级：{report['grade'] or '—'}** · 总分 {report['total_score'] if report['total_score'] is not None else '—'} / 100"
        f" · 数据覆盖率 {round((report['coverage'] or 0) * 100)}%",
        "",
        "| 维度 | 得分 |",
        "|---|---|",
    ]
    for key, d in report["sub_scores"].items():
        cell = "数据不足" if d["score"] is None else f"{d['score']} / {d['max']}"
        out.append(f"| {DIMENSION_LABELS[key]} | {cell} |")
    if report["red_flags"]:
        out += ["", "**⚠ 排雷提示**", ""] + [f"- {f['label']}：{f['detail']}" for f in report["red_flags"]]

    for i, sec in enumerate(report["sections"], 1):
        out += ["", f"## {i}. {sec['title']}", ""]
        for m in sec.get("metric_keys") or []:
            d = report["sub_scores"][m]
            out += [f"**{DIMENSION_LABELS[m]}**", ""] + _metric_table(d) + [""]
        if sec["key"] == "events":
            for e in sec.get("items") or []:
                out.append(f"- {e['date']} [{e['label']}] {e['title']}")
            out.append("")
        if sec.get("quarters"):
            out += _quarter_table(sec["quarters"], facts) + [""]
        if sec.get("info_facts"):
            out += ["**参考指标（不计分）**", "", "| 指标 | 数值 |", "|---|---|"]
            out += [f"| {facts[f]['label']} | {fmt(facts[f])} |" for f in sec["info_facts"] if f in facts]
            out.append("")
        if sec.get("peers"):
            p = sec["peers"]
            out += [f"**{p['industry']}行业市值相近公司**（行业共 {p['count']} 家，行情快照 {p['as_of']}）", "",
                    "| 公司 | 市值（亿元） | 市盈率 | 市净率 | ROE（TTM） | 股息率（近 12 个月） |",
                    "|---|---|---|---|---|---|"]
            for r in p["rows"]:
                pe = "亏损" if r.get("pe") is not None and r["pe"] <= 0 else _cell(r.get("pe"))
                name = f"**{r['name']}**" if r["self"] else r["name"]
                out.append(f"| {name} | {_cell(r.get('market_cap'))} | {pe} | {_cell(r.get('pb'))} | "
                           f"{_cell(r.get('roe'), '%')} | {_cell(r.get('dividend_yield'), '%')} |")
            out += ["", "ROE 为行情快照的 TTM 口径，与「盈利能力」一节的年报 ROE 不同。", ""]
        if sec.get("tracking") is not None:
            heads = {"flag": "排雷项", "metric": "指标", "event": "需跟进的公告", "risk": "公司自述风险", "next": "下一期数据"}
            for it in sec["tracking"]:
                val = f" {it['value']}" if it["value"] else ""
                line = f"- [{heads[it['kind']]}] **{it['label']}**{val}：{it['note']}"
                if it.get("explain"):
                    e = it["explain"]
                    line += f"（据 {e['doc']} 第 {e['page']} 页：{e['claim']}）"
                out.append(line)
            out.append("")
            continue
        if sec.get("kpi_table"):
            out += _kpi_markdown(sec["kpi_table"]) + [""]
        elif sec.get("kpis"):  # 旧版报告没有矩阵
            out += ["**经营数据（摘自定期报告）**", "", "| 项目 | 数值 | 出处 |", "|---|---|---|"]
            out += [f"| {k['name']} | {k['value']} {k['unit']} | {_doc(k)} 第 {k['page']} 页 |" for k in sec["kpis"]]
            out.append("")
        if sec.get("md"):
            out.append(sec["md"])
        elif sec["key"] != "events":
            out.append("_AI 解读不可用_")
        if sec.get("warning"):
            out += ["", f"> ⚠ {sec['warning']}"]
        if sec.get("citations"):
            out += ["", "定期报告原文："] + [f"- {_doc(c)} 第 {c['page']} 页：「{c['quote']}」" for c in sec["citations"]]
        if sec.get("company_risks"):
            out += ["", "**公司自述风险（定期报告原文）**", ""]
            for r in sec["company_risks"]:
                if r.get("list_only"):
                    out.append(f"- {_doc(r)} 第 {r['page']} 页列出的风险：{r['title']}（报告未逐条展开）")
                else:
                    out.append(f"- **{r['title']}**（{_doc(r)} 第 {r['page']} 页）：{r['quote']}")

    out += ["", f"## {len(report['sections']) + 1}. 附录：计分规则与数据来源", "", SCORING_RULES, ""]
    out += ["| 数据源 | 抓取时间 | 状态 |", "|---|---|---|"]
    for s_ in report["sources"]:
        out.append(f"| {s_['label']} | {(s_.get('fetched_at') or '—')[:16]} | {s_['status']} |")
    out += ["", f"> {DISCLAIMER}"]
    return "\n".join(out)
