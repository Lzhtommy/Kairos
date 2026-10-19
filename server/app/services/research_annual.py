"""定期报告深读：选报告 → 切「第三节 管理层讨论与分析」（季报切「变动的情况、原因」）→ 按小标题切块 → 选块
→ 校验 LLM 抽取的原文引用。

本模块只有纯函数（PDF 下载与 LLM 调用在流水线里），输入是 pypdf 抽出的逐页文本。
铁律延续 research_writer：LLM 只摘录，每条必须带原文引用，引用对不上原文的条目直接丢弃；
页码由代码按引用回查，不信 LLM。详见 docs/RESEARCH-DESIGN.md §10。
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_right
from collections.abc import Iterable
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------- 选报告

# kind → (标题正则, 报告期末月日, 简称)。年报正则要求「年」后紧跟「年度报告」，不会误中「半年度报告」
REPORT_KINDS: dict[str, tuple[re.Pattern, str, str]] = {
    "annual": (re.compile(r"(\d{4})\s*年\s*年度报告"), "12-31", "年报"),
    "semi": (re.compile(r"(\d{4})\s*年\s*(?:半年度|中期)报告"), "06-30", "半年报"),
    "q1": (re.compile(r"(\d{4})\s*年\s*第?一季度报告"), "03-31", "一季报"),
    "q3": (re.compile(r"(\d{4})\s*年\s*第?三季度报告"), "09-30", "三季报"),
}
_TITLE_EXCLUDE = ("摘要", "英文", "English", "提示性", "更正公告", "补充公告")


def pick_report(rows: list[dict], kind: str) -> dict | None:
    """从巨潮该类定期报告公告里挑最近一期的正文；同期多份（更正 / 修订）取公告时间最新。"""
    pattern, mmdd, short = REPORT_KINDS[kind]
    candidates = []
    for r in rows or []:
        title = str(r.get("公告标题") or "")
        m = pattern.search(title)
        if not m or title.startswith("关于") or any(w in title for w in _TITLE_EXCLUDE):
            continue
        link = str(r.get("公告链接") or "")
        qs = parse_qs(urlparse(link).query)
        aid = (qs.get("announcementId") or [None])[0]
        date = (qs.get("announcementTime") or [None])[0] or str(r.get("公告时间") or "")[:10]
        if not aid or not date:
            continue
        year = int(m.group(1))
        candidates.append({
            "kind": kind,
            "doc": f"{year} {short}",
            "period_end": f"{year}-{mmdd}",
            "title": title,
            "date": date[:10],
            "year": year,
            "announcement_id": aid,
            "pdf_url": f"http://static.cninfo.com.cn/finalpage/{date[:10]}/{aid}.PDF",
            "detail_url": link,
        })
    if not candidates:
        return None
    return max(candidates, key=lambda c: (c["year"], c["date"]))


def choose_reports(metas: dict[str, dict | None]) -> list[str]:
    """要读哪几份，按报告期由新到旧：年报 + 比它新的半年报 + 比两者都新的季报。

    4–8 月 = 年报 + 一季报；8–10 月 = 年报 + 半年报；10 月 – 次年 4 月 = 年报 + 半年报 + 三季报
    （三季报的原因表很简略，上半年发生的事仍靠半年报解释）；新年报发布后只读年报。
    """
    chosen: list[dict] = []
    annual = metas.get("annual")
    base = annual["period_end"] if annual else ""
    semi = metas.get("semi")
    if semi and semi["period_end"] > base:
        chosen.append(semi)
        base = semi["period_end"]
    quarters = [m for m in (metas.get("q1"), metas.get("q3")) if m]
    if quarters:
        q = max(quarters, key=lambda m: m["period_end"])
        if q["period_end"] > base:
            chosen.append(q)
    if annual:
        chosen.append(annual)
    return [m["kind"] for m in sorted(chosen, key=lambda m: m["period_end"], reverse=True)]


# ---------------------------------------------------------------- 切 MD&A

# 目录页的条目后面跟着「……5」，行尾不是标题结束，所以要求标题独占到行尾
_MDNA_START = re.compile(r"^[ \t]*第三节\s*管理层讨论与分析[ \t]*$", re.M)
_MDNA_END = re.compile(r"^[ \t]*第四节\s*\S", re.M)


MAX_PAGES = 200  # MD&A 一般在前 60 页；找不到第三节时最多读这么多页就放弃


def read_until_mdna_end(page_texts: Iterable[str]) -> list[str]:
    """逐页消费文本，读到第三节之后的「第四节」所在页即停，避免解析整本年报（含几百页财务附注）。"""
    out: list[str] = []
    started = False
    for text in page_texts:
        out.append(text or "")
        if not started:
            m = _MDNA_START.search(text or "")
            if m:
                started = True
                if _MDNA_END.search(text[m.end():]):
                    break
        elif _MDNA_END.search(text or ""):
            break
        if len(out) >= MAX_PAGES:
            break
    return out


_PAGE_NO = re.compile(r"^\s*\d+\s*/\s*\d+\s*$")
# 「□适用 √不适用」这类勾选框行（立讯的勾号是私用区字符 ）
_CHECKBOX = re.compile(r"^[\s□√■]*适用[\s□√■]*不适用\s*$")


def _strip_furniture(pages: list[tuple[int, str]], all_pages: list[str]) -> list[tuple[int, str]]:
    """去页眉（全文半数以上页面都出现的同一行）、页脚页码（「9 / 143」）和勾选框行。

    页眉按整份文档统计：季报的变动原因只占两三页，只看这几页统计不出来。
    """
    counts: dict[str, int] = {}
    for text in all_pages:
        for line in {ln.strip() for ln in (text or "").splitlines()}:
            if line:
                counts[line] = counts.get(line, 0) + 1
    n_pages = len(all_pages)
    header = {ln for ln, n in counts.items() if n_pages >= 3 and n * 2 >= n_pages and len(ln) >= 4}
    out = []
    for no, text in pages:
        lines = [
            ln for ln in text.splitlines()
            if ln.strip() not in header and not _PAGE_NO.match(ln) and not _CHECKBOX.match(ln)
        ]
        out.append((no, "\n".join(lines)))
    return out


def extract_mdna(pages: list[str]) -> list[tuple[int, str]] | None:
    """返回 [(页码, 该页属于 MD&A 的文本)]，页码从 1 开始，已去页眉页脚；找不到第三节返回 None。"""
    start_page = None
    out: list[tuple[int, str]] = []
    for i, text in enumerate(pages):
        if start_page is None:
            m = _MDNA_START.search(text or "")
            if not m:
                continue
            start_page = i
            text = text[m.end():]
        end = _MDNA_END.search(text or "")
        if end:
            out.append((i + 1, text[: end.start()]))
            break
        out.append((i + 1, text or ""))
    return _strip_furniture(out, pages) if out else None


# 季报没有 MD&A，唯一有归因的是「（三）主要会计数据、财务指标发生变动的情况、原因」；
# 其后到「二、股东信息」之间沪市行业公司还有「（四）销售情况」「（五）经销商情况」等，一并保留
_Q_START = re.compile(r"^[ \t]*[（(]三[）)]\s*主要会计数据、财务指标发生变动的情况、原因[ \t]*$", re.M)
_Q_END = re.compile(r"^[ \t]*二、\s*股东信息", re.M)


def extract_quarterly(pages: list[str]) -> list[tuple[int, str]] | None:
    """季报「变动的情况、原因」一节：[(页码, 文本)]，已去页眉页脚；找不到返回 None。"""
    out: list[tuple[int, str]] = []
    started = False
    for i, text in enumerate(pages):
        text = text or ""
        if not started:
            m = _Q_START.search(text)
            if not m:
                continue
            started = True
            text = text[m.end():]
        end = _Q_END.search(text)
        if end:
            out.append((i + 1, text[: end.start()]))
            break
        out.append((i + 1, text))
    if not out:
        return None
    return _strip_furniture(out, pages)


# ---------------------------------------------------------------- 切块

_HEADING = re.compile(
    r"^[ \t]*(?:[一二三四五六七八九十]+、|[（(][一二三四五六七八九十]+[）)])[ \t]*(\S.{0,59}?)[ \t]*$", re.M
)

# 要抽取的块：key → 小标题关键词（沪深两种格式都覆盖）
BLOCK_RULES: list[tuple[str, re.Pattern]] = [
    ("business", re.compile(r"从事的(?:主要)?业务|所属行业及主营业务")),
    ("industry", re.compile(r"所处行业情况")),
    ("discussion", re.compile(r"经营情况的?讨论与分析")),
    ("core", re.compile(r"核心竞争力")),
    ("main_biz", re.compile(r"^主营业务分析")),
    ("balance", re.compile(r"^资产(?:、|及)?负债(?:状况|情况)?分析")),
    ("industry_ops", re.compile(r"行业经营性信息")),
    ("landscape", re.compile(r"行业格局")),
    ("strategy", re.compile(r"发展战略")),
    ("plan", re.compile(r"^经营计划")),
    ("risks", re.compile(r"可能面对的风险|面临的风险")),
    ("outlook", re.compile(r"未来发展的(?:讨论与分析|展望)")),
    # 「质量回报双提升」「市值管理」执行情况常带一段业绩回顾与原因（金徽酒半年报「补缴税款」就在这里）
    ("disclosure", re.compile(r"其他披露事项|质量回报|市值管理|估值提升")),
]

# 只作边界、不抽取的小标题——让上一个块在这里截断
_STOP = re.compile(
    r"非主营业务|投资状况|重大资产和股权出售|控股参股公司|结构化主体|接待调研|"
    r"不适用准则|主要经营情况|^其他$"
)

BLOCK_LABELS = {
    "business": "报告期内从事的业务", "industry": "所处行业情况", "discussion": "经营情况讨论与分析",
    "core": "核心竞争力", "main_biz": "主营业务分析", "industry_ops": "行业经营性信息",
    "landscape": "行业格局和趋势", "strategy": "发展战略", "plan": "经营计划", "risks": "可能面对的风险",
    "outlook": "未来发展的展望", "balance": "资产及负债状况分析", "disclosure": "其他披露事项（含质量回报行动方案）",
    "changes": "主要会计数据、财务指标变动原因",  # 季报
}


def _join(pages: list[tuple[int, str]]) -> tuple[str, list[int], list[int]]:
    starts, nums, parts, pos = [], [], [], 0
    for no, text in pages:
        starts.append(pos)
        nums.append(no)
        parts.append(text)
        pos += len(text) + 1
    return "\n".join(parts), starts, nums


def split_blocks(mdna: list[tuple[int, str]]) -> list[dict]:
    """按已识别的小标题切块：[{key, title, page, text}]，原文顺序。

    边界 = 已识别的块标题 + 停止标题；块内的其他同级小标题（如立讯「一、消费电子业务」）不切。
    同一 key 出现多次（如「五、其他披露事项」空父标题 +「（二）其他披露事项」）时正文合并，页码取第一次。
    """
    text, starts, nums = _join(mdna)
    marks: list[tuple[int, int, str | None, str]] = []  # (标题起点, 正文起点, key|None, 标题)
    for m in _HEADING.finditer(text):
        title = m.group(1).strip()
        key = next((k for k, pat in BLOCK_RULES if pat.search(title)), None)
        if key is not None or _STOP.search(title):
            marks.append((m.start(), m.end(), key, title))

    by_key: dict[str, dict] = {}
    for i, (h_start, body_start, key, title) in enumerate(marks):
        if key is None:
            continue
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        body = text[body_start:end].strip()
        if key in by_key:
            by_key[key]["text"] = f"{by_key[key]['text']}\n{body}".strip()
            continue
        page = nums[bisect_right(starts, h_start) - 1]
        by_key[key] = {"key": key, "title": title, "page": page, "text": body}
    return list(by_key.values())


# 主营业务分析里有「XX 变动原因说明」、资产负债分析里有「注 N：XX 变动主要原因」——归因密度最高，排前面
SELECT_PRIORITY = [
    "discussion", "risks", "main_biz", "balance", "industry_ops", "outlook", "plan", "strategy",
    "disclosure", "core", "business", "landscape", "industry",
]
MIN_BLOCK = 30  # 太短的块（如只是父标题）不值得占篇幅
MIN_TAIL = 500  # 预算剩余不足此数就不再截断塞入


def select_blocks(blocks: list[dict], budget: int = 30000, block_cap: int = 8000) -> list[dict]:
    """按优先级在字数预算内选块，单块超 block_cap 截断（标 truncated），返回原文顺序。"""
    order = {id(b): i for i, b in enumerate(blocks)}
    by_key = {b["key"]: b for b in blocks}
    picked: list[dict] = []
    left = budget
    for key in SELECT_PRIORITY:
        b = by_key.get(key)
        if b is None or len(b["text"]) < MIN_BLOCK:
            continue
        limit = min(block_cap, left)
        if limit < min(MIN_TAIL, len(b["text"])):
            continue
        text = b["text"][:limit]
        picked.append({**b, "text": text, "truncated": len(text) < len(b["text"]), "_i": order[id(b)]})
        left -= len(text)
        if left <= 0:
            break
    picked.sort(key=lambda b: b.pop("_i"))
    return picked


QUARTERLY_CAP = 8000


def report_blocks(kind: str, pages: list[tuple[int, str]]) -> list[dict]:
    """交给 LLM 抽取的块：年报 / 半年报按小标题切块选块；季报整节一块。"""
    if kind in ("q1", "q3"):
        text = "\n".join(t for _, t in pages).strip()
        return [{"key": "changes", "title": BLOCK_LABELS["changes"], "page": pages[0][0],
                 "text": text[:QUARTERLY_CAP], "truncated": len(text) > QUARTERLY_CAP}] if text else []
    return select_blocks(split_blocks(pages))


def merge_extracts(docs: list[tuple[dict, dict]]) -> dict:
    """合并多份报告的抽取结果。docs = [(meta, verify_extract 输出)]，按报告期由新到旧。

    drivers / kpis 全保留并标出处 doc（如「2026 半年报」）；risks 取最新一份非空的年报 / 半年报
    （季报不披露风险；半年报常写「与年报相比无重大变化」，此时抽不出条目，自然退回年报）。
    """
    out: dict = {"drivers": [], "kpis": [], "risks": [], "reports": []}
    for meta, ex in docs:
        tag = {"doc": meta.get("doc") or "定期报告"}
        out["drivers"] += [{**d, **tag} for d in ex.get("drivers") or []]
        out["kpis"] += [{**k, **tag} for k in ex.get("kpis") or []]
        if not out["risks"] and meta.get("kind", "annual") in ("annual", "semi"):
            out["risks"] = [{**r, **tag} for r in ex.get("risks") or []]
        out["reports"].append(meta)
    return out


# ---------------------------------------------------------------- 原文引用校验

DRIVER_TOPICS = {"revenue", "margin", "expense", "cashflow", "capex", "inventory", "balance", "other"}
MIN_QUOTE = 6  # 归一化后的最短引用长度，防止「收入」这类到处都能匹配的碎片
# 千分位数字整体匹配；不能先去空白再抽数——表格里相邻单元格「24,770.35 315,207.51」会粘成一个数
_NUM = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def norm(s: str) -> str:
    """NFKC（全角→半角）+ 去全部空白：pypdf 会在句中断行、插空格。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def _numbers(s: str) -> set[float]:
    return {float(x.replace(",", "")) for x in _NUM.findall(unicodedata.normalize("NFKC", s or ""))}


def locate_page(quote: str, pages: list[tuple[int, str]]) -> int | None:
    """引用在原文中的起始页码；找不到返回 None。跨页的引用也能找到（按起始页算）。"""
    q = norm(quote)
    if not q:
        return None
    starts, nums, parts, pos = [], [], [], 0
    for no, text in pages:
        t = norm(text)
        starts.append(pos)
        nums.append(no)
        parts.append(t)
        pos += len(t)
    idx = "".join(parts).find(q)
    if idx < 0:
        return None
    return nums[bisect_right(starts, idx) - 1]


def _str(d: dict, key: str) -> str:
    v = d.get(key)
    return v.strip() if isinstance(v, str) else ""


def verify_extract(raw: dict, pages: list[tuple[int, str]]) -> dict:
    """校验 LLM 抽取结果，返回 {drivers, kpis, risks, dropped}；每条补上代码回查的 page。

    - quote 归一化后须是原文子串，且不短于 MIN_QUOTE；
    - driver.claim 里的数字必须都出现在其 quote 中，否则 claim 退回为 quote 原文（防止改写时改数 / 借表格里的数，
      但条目本身已由 quote 证实，不丢——年报「变动原因说明」常不带数字，LLM 爱把上方表格的变动比例拼进 claim）；
    - kpi.value 至少含一个数字，且都出现在其 quote 中。
    """
    out: dict = {"drivers": [], "kpis": [], "risks": [], "dropped": 0}

    def items(key: str) -> list:
        v = raw.get(key) if isinstance(raw, dict) else None
        return v if isinstance(v, list) else []

    def quoted_page(d: dict) -> int | None:
        quote = _str(d, "quote")
        if len(norm(quote)) < MIN_QUOTE:
            return None
        return locate_page(quote, pages)

    for d in items("drivers"):
        page = quoted_page(d) if isinstance(d, dict) else None
        claim = _str(d, "claim") if isinstance(d, dict) else ""
        if page is None:
            out["dropped"] += 1
            continue
        if not claim or not _numbers(claim) <= _numbers(d["quote"]):
            claim = _str(d, "quote")
        topic = _str(d, "topic")
        out["drivers"].append({
            "topic": topic if topic in DRIVER_TOPICS else "other",
            "claim": claim, "quote": _str(d, "quote"), "page": page,
        })

    for d in items("kpis"):
        page = quoted_page(d) if isinstance(d, dict) else None
        if page is None or not _str(d, "name"):
            out["dropped"] += 1
            continue
        value_nums = _numbers(_str(d, "value"))
        if not value_nums or not value_nums <= _numbers(d["quote"]):
            out["dropped"] += 1
            continue
        out["kpis"].append({
            "name": _str(d, "name"), "value": _str(d, "value"), "unit": _str(d, "unit"),
            "quote": _str(d, "quote"), "page": page,
        })

    for d in items("risks"):
        page = quoted_page(d) if isinstance(d, dict) else None
        if page is None or not _str(d, "title"):
            out["dropped"] += 1
            continue
        out["risks"].append({"title": _str(d, "title"), "quote": _str(d, "quote"), "page": page})

    return out
