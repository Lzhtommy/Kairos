"""年报深读：选年报 → 切「第三节 管理层讨论与分析」→ 按小标题切块 → 选块 → 校验 LLM 抽取的原文引用。

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

# ---------------------------------------------------------------- 选年报

_TITLE_YEAR = re.compile(r"(\d{4})\s*年\s*年度报告")
_TITLE_EXCLUDE = ("摘要", "英文", "English", "提示性", "更正公告", "补充公告")


def pick_annual_report(rows: list[dict]) -> dict | None:
    """从巨潮「年报」类公告里挑最近一个完整年度的年报正文；同年多份（更正 / 修订）取公告时间最新。"""
    candidates = []
    for r in rows or []:
        title = str(r.get("公告标题") or "")
        m = _TITLE_YEAR.search(title)
        if not m or title.startswith("关于") or any(w in title for w in _TITLE_EXCLUDE):
            continue
        link = str(r.get("公告链接") or "")
        qs = parse_qs(urlparse(link).query)
        aid = (qs.get("announcementId") or [None])[0]
        date = (qs.get("announcementTime") or [None])[0] or str(r.get("公告时间") or "")[:10]
        if not aid or not date:
            continue
        candidates.append({
            "title": title,
            "date": date[:10],
            "year": int(m.group(1)),
            "announcement_id": aid,
            "pdf_url": f"http://static.cninfo.com.cn/finalpage/{date[:10]}/{aid}.PDF",
            "detail_url": link,
        })
    if not candidates:
        return None
    return max(candidates, key=lambda c: (c["year"], c["date"]))


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


def _strip_furniture(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """去页眉（半数以上页面都出现的同一行）、页脚页码（「9 / 143」）和勾选框行。"""
    counts: dict[str, int] = {}
    for _, text in pages:
        for line in {ln.strip() for ln in text.splitlines()}:
            if line:
                counts[line] = counts.get(line, 0) + 1
    header = {ln for ln, n in counts.items() if len(pages) >= 3 and n * 2 >= len(pages) and len(ln) >= 4}
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
    return _strip_furniture(out) if out else None


# ---------------------------------------------------------------- 切块

_HEADING = re.compile(
    r"^[ \t]*(?:[一二三四五六七八九十]+、|[（(][一二三四五六七八九十]+[）)])[ \t]*(\S.{0,59}?)[ \t]*$", re.M
)

# 要抽取的块：key → 小标题关键词（沪深两种格式都覆盖）
BLOCK_RULES: list[tuple[str, re.Pattern]] = [
    ("business", re.compile(r"从事的(?:主要)?业务")),
    ("industry", re.compile(r"所处行业情况")),
    ("discussion", re.compile(r"经营情况讨论与分析")),
    ("core", re.compile(r"核心竞争力")),
    ("main_biz", re.compile(r"^主营业务分析")),
    ("industry_ops", re.compile(r"行业经营性信息")),
    ("landscape", re.compile(r"行业格局")),
    ("strategy", re.compile(r"发展战略")),
    ("plan", re.compile(r"^经营计划")),
    ("risks", re.compile(r"可能面对的风险|面临的风险")),
    ("outlook", re.compile(r"未来发展的(?:讨论与分析|展望)")),
]

# 只作边界、不抽取的小标题——让上一个块在这里截断
_STOP = re.compile(
    r"非主营业务|资产(?:、|及)?负债|投资状况|重大资产和股权出售|控股参股公司|结构化主体|接待调研|"
    r"市值管理|质量回报|不适用准则|主要经营情况|^其他$"
)

BLOCK_LABELS = {
    "business": "报告期内从事的业务", "industry": "所处行业情况", "discussion": "经营情况讨论与分析",
    "core": "核心竞争力", "main_biz": "主营业务分析", "industry_ops": "行业经营性信息",
    "landscape": "行业格局和趋势", "strategy": "发展战略", "plan": "经营计划", "risks": "可能面对的风险",
    "outlook": "未来发展的展望",
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
    同一 key 只取第一次出现。
    """
    text, starts, nums = _join(mdna)
    marks: list[tuple[int, int, str | None, str]] = []  # (标题起点, 正文起点, key|None, 标题)
    seen: set[str] = set()
    for m in _HEADING.finditer(text):
        title = m.group(1).strip()
        key = next((k for k, pat in BLOCK_RULES if pat.search(title)), None)
        if key is None:
            if _STOP.search(title):
                marks.append((m.start(), m.end(), None, title))
            continue
        if key in seen:
            continue
        seen.add(key)
        marks.append((m.start(), m.end(), key, title))

    blocks = []
    for i, (h_start, body_start, key, title) in enumerate(marks):
        if key is None:
            continue
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        page = nums[bisect_right(starts, h_start) - 1]
        blocks.append({"key": key, "title": title, "page": page, "text": text[body_start:end].strip()})
    return blocks


SELECT_PRIORITY = [
    "discussion", "industry_ops", "risks", "outlook", "plan", "strategy",
    "core", "business", "landscape", "main_biz", "industry",
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


# ---------------------------------------------------------------- 原文引用校验

DRIVER_TOPICS = {"revenue", "margin", "expense", "cashflow", "capex", "inventory", "other"}
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
    - driver.claim 里的数字必须都出现在其 quote 中（防止改写时改数）；
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
        if page is None or not claim or not _numbers(claim) <= _numbers(d["quote"]):
            out["dropped"] += 1
            continue
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
