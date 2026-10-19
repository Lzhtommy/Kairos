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

from app.core.config import settings
from app.services.research_annual import BLOCK_LABELS, verify_extract
from app.services.research_facts import fmt
from app.services.research_score import DIMENSION_LABELS

logger = logging.getLogger("kairos.research")

DISCLAIMER = "本报告由程序基于公开数据自动生成，仅供研究参考，不构成投资建议。"

# (key, 标题, 指标表对应的评分维度, 该章可用的 fact group)
SECTIONS: list[tuple[str, str, list[str], list[str]]] = [
    ("summary", "摘要", [], []),
    ("overview", "公司概况与主营构成", [], ["segment"]),
    ("profitability", "盈利能力", ["profitability"], ["profitability"]),
    ("growth", "成长性", ["growth"], ["growth"]),
    ("health", "财务健康与现金流", ["health", "cashflow"], ["health", "cashflow"]),
    ("valuation", "估值与股东回报", ["valuation", "shareholder"], ["valuation", "shareholder"]),
    ("events", "近期重大公告", ["events"], []),
    ("news", "新闻与机构观点", [], ["consensus"]),
    ("risks", "风险提示", [], ["profitability", "growth", "health", "cashflow", "valuation", "shareholder"]),
]

BANNED = [
    "建议买入", "建议卖出", "建议增持", "建议减持", "建议持有", "目标价", "强烈推荐", "推荐买入",
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
    "2. 不要逐条复述数字，重点解释数字背后的含义与经营的关系。背景材料里有「公司解释（摘自年报）」时，"
    "用它说明变化原因并注明「据年报」；没有公司解释的变化只客观陈述现象，不要自行推测原因。\n"
    "3. 正文是写给读者的研报，不要提及你拿到的材料本身：禁止出现「事实中」「背景材料」「未提供解释」"
    "「故此处仅陈述」「需结合公告进一步确认」这类说明材料范围的话。\n"
    "4. 不给任何投资建议：禁止出现买入、卖出、增减持建议、目标价、仓位等表述。\n"
    "5. 中文，客观克制，200~350 字，Markdown 段落（可用少量无序列表），不要写章节标题。\n"
    "输出严格 JSON：{\"markdown\": \"正文\"}"
)


# ---------------------------------------------------------------- 校验

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_DATE_RE = re.compile(r"\d{4}\s*[-~—至到]\s*\d{4}|\d{4}[-/年.]\d{1,2}(?:[-/月.]\d{1,2}日?)?|\d{4}\s*年(?:度)?|[QH]\d|第[一二三四]季度")


def allowed_numbers(facts: dict, extra_texts: list[str]) -> set[float]:
    out: set[float] = set()
    for f in facts.values():
        # 标签里也有数字（「近 3 年」「509 个交易日」「57 家」），同样允许引用
        for m in _NUM_RE.findall(f.get("label") or ""):
            out.add(round(float(m), 2))
        v = f.get("value")
        if v is None:
            continue
        for d in (0, 1, 2):
            out.add(round(abs(v), d))
    for t in extra_texts:
        for m in _NUM_RE.findall(t):
            out.add(round(float(m), 2))
    return out


def find_bad_numbers(md: str, allowed: set[float]) -> list[str]:
    text = _DATE_RE.sub(" ", md)
    bad = []
    for m in _NUM_RE.findall(text):
        x = float(m)
        if x == int(x) and (0 <= x <= 10 or 1990 <= x <= 2100):
            continue  # 计数词、年份
        decimals = len(m.split(".")[1]) if "." in m else 0
        if not any(abs(round(a, decimals) - x) < 1e-9 for a in allowed):
            bad.append(m)
    return bad


def find_banned(md: str) -> list[str]:
    return [w for w in BANNED if w in md]


def find_meta(md: str) -> list[str]:
    return [w for w in META_PHRASES if w in md]


# ---------------------------------------------------------------- LLM


def llm_enabled() -> bool:
    return bool(settings.deepseek_api_key) and settings.ai_provider in ("auto", "deepseek")


def _chat_json(messages: list[dict], max_tokens: int = 1500) -> tuple[dict, int]:
    # 网络抖动（SSL EOF / 连接被断）重试两次；HTTP 4xx/5xx 与 JSON 解析错误直接抛
    for attempt in range(3):
        try:
            return _chat_json_once(messages, max_tokens)
        except httpx.TransportError as exc:
            if attempt == 2:
                raise
            logger.info("DeepSeek transport error (%s), retrying", exc)
            time.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")


def _chat_json_once(messages: list[dict], max_tokens: int = 1500) -> tuple[dict, int]:
    r = httpx.post(
        f"{settings.deepseek_base_url}/chat/completions",
        headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
        json={
            "model": settings.deepseek_model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
            "temperature": 0.2,
        },
        timeout=90,
    )
    r.raise_for_status()
    body = r.json()
    tokens = int((body.get("usage") or {}).get("total_tokens") or 0)
    return json.loads(body["choices"][0]["message"]["content"]), tokens


def _write_validated(user_prompt: str, key: str, allowed: set[float]) -> tuple[dict, int, str | None]:
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
        problems += [f"禁用表述：{w}" for w in find_banned(text)]
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

EXTRACT_VERSION = "a2"  # 改抽取提示词 / 校验规则时升版本，旧缓存自动失效

EXTRACT_PROMPT = (
    "你是上市公司年报信息摘录员。从给定的年报「管理层讨论与分析」片段中摘录信息，只摘录、不评价、不推断。规则：\n"
    "1. 每条都必须带 quote：从原文逐字复制的一段连续文字（20~120 字），不得改写、拼接、省略中间内容。\n"
    "2. drivers：公司对经营变化原因的解释（收入、毛利、费用、现金流、资本开支、存货等）。"
    "topic 取 revenue / margin / expense / cashflow / capex / inventory / other 之一；"
    "claim 用一句话概括（≤60 字），只能使用 quote 里出现的数字，不要把表格里的变动比例等 quote 之外的数字加进来。"
    "年报「XX 变动原因说明」每个科目各算一条。最多 15 条。\n"
    "3. kpis：公司披露的经营数据（销量、产量、产能、库存、渠道或门店数量、直销占比、在手订单等）。"
    "value 只写数字，照抄原文；unit 写单位；name 写清口径（如「茅台酒销量」）。"
    "也摘录分地区（如省内 / 省外、境内 / 境外）、分销售渠道（经销 / 直销 / 线上）的营业收入及其同比，"
    "name 写清口径（如「省外营业收入」「省外营业收入同比」）。"
    "不要摘录营业收入、净利润等合并报表总额科目。最多 20 条。\n"
    "4. risks：公司自述的经营风险。title 为风险名称（≤12 字），quote 为原文。最多 8 条。\n"
    "5. 原文没有的类别返回空数组，不要凑数。\n"
    "输出严格 JSON：{\"drivers\": [{\"topic\", \"claim\", \"quote\"}], "
    "\"kpis\": [{\"name\", \"value\", \"unit\", \"quote\"}], \"risks\": [{\"title\", \"quote\"}]}"
)


def extract_annual(blocks: list[dict], pages: list[tuple[int, str]]) -> tuple[dict, int]:
    """一次 LLM 调用抽取 drivers / kpis / risks，再由 verify_extract 按原文校验。"""
    text = "\n\n".join(
        f"【{BLOCK_LABELS.get(b['key'], b['title'])}】（年报第 {b['page']} 页起{'，已截断' if b.get('truncated') else ''}）\n{b['text']}"
        for b in blocks
    )
    messages = [{"role": "system", "content": EXTRACT_PROMPT}, {"role": "user", "content": text}]
    raw, tokens = _chat_json(messages, max_tokens=4000)
    return verify_extract(raw, pages), tokens


# 各章分配哪些 topic 的公司解释
SECTION_TOPICS = {
    "profitability": {"margin", "expense"},
    "growth": {"revenue", "other"},
    "health": {"cashflow", "capex", "inventory"},
}


def _citations(key: str, annual: dict | None) -> list[dict]:
    topics = SECTION_TOPICS.get(key)
    if not annual or not topics:
        return []
    return [d for d in annual.get("drivers") or [] if d["topic"] in topics]


def _annual_block(cites: list[dict]) -> str:
    return "公司解释（摘自年报）：\n" + "\n".join(
        f"- {c['claim']}（第 {c['page']} 页原文：「{c['quote']}」）" for c in cites
    )


def _facts_block(facts: dict, groups: list[str]) -> str:
    lines = []
    for f in facts.values():
        if f.get("group") in groups:
            period = f"（{f['period']}）" if f.get("period") else ""
            lines.append(f"- {f['label']}{period}：{fmt(f)}")
    return "\n".join(lines) or "（无）"


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
        ) + "\n其他公告标题：\n" + ("\n".join(f"- {e['date']} {e['title']}" for e in others) or "（无）")
        return text, [text]
    if key == "news":
        news = "\n".join(f"- {n['time'][:10]} {n['title']}（{n['source']}）" for n in dataset.get("news") or [])
        cons = dataset.get("consensus") or {}
        titles = "\n".join(
            f"- {t['date']} {t['org']}《{t['title']}》评级：{t['rating']}" for t in cons.get("titles") or []
        )
        text = f"近期新闻：\n{news or '（无）'}\n近期机构研报：\n{titles or '（无）'}"
        return text, [text]
    if key == "risks":
        flags = "\n".join(f"- {f['label']}：{f['detail']}" for f in result["red_flags"]) or "（无）"
        neg = "\n".join(
            f"- {e['date']} [{e['label']}] {e['title']}"
            for e in dataset.get("events") or []
            if e["tag"] in ("reduce", "inquiry", "investigation", "forecast_down", "lawsuit")
        ) or "（无）"
        text = f"各维度得分：\n{_score_block(result)}\n排雷项：\n{flags}\n负面公告：\n{neg}"
        return text, [text]
    return "", []


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
    dataset: dict, result: dict, name: str, code: str, progress=None, annual: dict | None = None
) -> tuple[list[dict], int]:
    """annual：年报深读结果（verify_extract 输出 + report 元信息），可为 None。"""
    facts = dataset["facts"]
    sections: list[dict] = []
    tokens = 0
    use_llm = llm_enabled()
    llm_keys = [k for k, *_ in SECTIONS]

    for i, (key, title, metric_keys, groups) in enumerate(SECTIONS, 1):
        if progress:
            progress(f"撰写 {i}/{len(llm_keys)}：{title}")
        sec: dict = {"key": key, "title": title, "md": "", "metric_keys": metric_keys, "ai": use_llm}
        if key == "events":
            sec["items"] = [e for e in dataset.get("events") or [] if e["tag"] != "other"][:30]
        if key == "news":
            sec["items"] = dataset.get("news") or []
            sec["reports"] = (dataset.get("consensus") or {}).get("titles") or []
        if key == "overview":
            sec["profile"] = dataset.get("profile") or {}
            sec["segments"] = dataset.get("segments") or []
            sec["kpis"] = (annual or {}).get("kpis") or []
        if key == "risks":
            sec["company_risks"] = (annual or {}).get("risks") or []

        context, extra = _context(key, dataset, result)
        cites = _citations(key, annual)
        if cites:
            sec["citations"] = cites
            block = _annual_block(cites)
            context = f"{context}\n{block}" if context else block
            extra = extra + [c["quote"] for c in cites] + [str(c["page"]) for c in cites]
        if key == "overview" and sec["kpis"]:
            block = "公司披露的经营数据（摘自年报）：\n" + "\n".join(
                f"- {k['name']}：{k['value']}{k['unit']}（第 {k['page']} 页）" for k in sec["kpis"]
            )
            context = f"{context}\n{block}" if context else block
            extra = extra + [block]
        if key == "summary":
            all_facts = _facts_block(facts, ["profitability", "growth", "health", "cashflow", "valuation", "shareholder"])
            flags = "\n".join(f"- {f['label']}：{f['detail']}" for f in result["red_flags"]) or "（无）"
            prompt = (
                f"股票：{name}（{code}）。基本面总分 {result['total_score']}，评级 {result['grade'] or '—'}。\n"
                f"各维度得分：\n{_score_block(result)}\n排雷项：\n{flags}\n事实：\n{all_facts}\n\n"
                "任务：提炼 3 条最重要的核心观察（每条 40~80 字，必须引用事实中的数字），"
                "输出 JSON：{\"observations\": [\"…\", \"…\", \"…\"]}"
            )
            observations = _fallback_observations(result)
            if use_llm:
                try:
                    allowed = allowed_numbers(facts, [flags, _score_block(result), str(result["total_score"])])
                    payload, used, warning = _write_validated(prompt, key, allowed)
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
            prompt = (
                f"股票：{name}（{code}）。本章：{title}。\n"
                f"事实：\n{_facts_block(facts, groups)}\n"
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
                payload, used, warning = _write_validated(prompt, key, allowed)
                tokens += used
                sec["md"] = str(payload.get("markdown") or "").strip()
                sec["warning"] = warning
            except Exception as exc:  # noqa: BLE001 — 单章失败不拖垮整份报告
                logger.warning("Research section %s LLM failed: %s", key, exc)
                sec["ai"] = False
        sections.append(sec)
    return sections, tokens


def render_markdown(report: dict) -> str:
    """全文 Markdown（导出用）。report 含 name/code/industry/data_as_of/total_score/grade/
    coverage/sub_scores/red_flags/sections/sources/scoring_version。"""
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
            out += [f"**{DIMENSION_LABELS[m]}**", "", "| 指标 | 数值 | 得分 |", "|---|---|---|"]
            for it in d["items"]:
                pts = "—" if it["points"] is None else (f"{it['points']}" if it["max"] is None else f"{it['points']} / {it['max']}")
                out.append(f"| {it['label']} | {it['display']} | {pts} |")
            out.append("")
        if sec["key"] == "events":
            for e in sec.get("items") or []:
                out.append(f"- {e['date']} [{e['label']}] {e['title']}")
            out.append("")
        if sec.get("kpis"):
            out += ["**经营数据（摘自年报）**", "", "| 项目 | 数值 | 出处 |", "|---|---|---|"]
            out += [f"| {k['name']} | {k['value']} {k['unit']} | 年报第 {k['page']} 页 |" for k in sec["kpis"]]
            out.append("")
        if sec.get("md"):
            out.append(sec["md"])
        elif sec["key"] != "events":
            out.append("_AI 解读不可用_")
        if sec.get("warning"):
            out += ["", f"> ⚠ {sec['warning']}"]
        if sec.get("citations"):
            out += ["", "年报原文："] + [f"- 第 {c['page']} 页：「{c['quote']}」" for c in sec["citations"]]
        if sec.get("company_risks"):
            out += ["", "**公司自述风险（年报原文）**", ""]
            out += [f"- **{r['title']}**（第 {r['page']} 页）：{r['quote']}" for r in sec["company_risks"]]

    out += ["", f"## {len(report['sections']) + 1}. 附录：评分明细与数据来源", ""]
    out += ["| 维度 | 指标 | 数值 | 得分 | 零分点 → 满分点 |", "|---|---|---|---|---|"]
    for key, d in report["sub_scores"].items():
        for it in d["items"]:
            rng = "—" if it["zero"] is None else f"{it['zero']} → {it['full']}"
            pts = "—" if it["points"] is None else f"{it['points']}"
            out.append(f"| {DIMENSION_LABELS[key]} | {it['label']} | {it['display']} | {pts} | {rng} |")
    out += ["", "| 数据源 | 抓取时间 | 状态 |", "|---|---|---|"]
    for s in report["sources"]:
        out.append(f"| {s['label']} | {(s.get('fetched_at') or '—')[:16]} | {s['status']} |")
    out += ["", f"> {DISCLAIMER}"]
    return "\n".join(out)
