"""定期报告深读：选报告 / 切 MD&A / 切季报变动原因 / 切块 / 选块 / 原文引用校验（docs/RESEARCH-DESIGN.md §10）。

fixtures 是 pypdf 从真实定期报告抽出的逐页文本：
- annual_*：2025 年报，到「第四节」所在页为止；600519 贵州茅台（沪市格式）、002475 立讯精密（深市格式）
- semi_603919：金徽酒 2026 半年报，到「第四节」所在页为止
- q1_*：2026 一季报，到「二、股东信息」之后一页为止
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.research_annual import (
    choose_reports,
    extract_mdna,
    extract_quarterly,
    locate_page,
    pick_report,
    read_until_mdna_end,
    select_blocks,
    split_blocks,
    verify_extract,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load_pages(code: str, name: str = "annual_{code}_2025") -> list[str]:
    return json.loads((FIXTURES / f"{name.format(code=code)}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def mt() -> list[tuple[int, str]]:
    mdna = extract_mdna(load_pages("600519"))
    assert mdna is not None
    return mdna


@pytest.fixture(scope="module")
def lx() -> list[tuple[int, str]]:
    mdna = extract_mdna(load_pages("002475"))
    assert mdna is not None
    return mdna


# ---------------------------------------------------------------- 选年报


def _row(title: str, date: str, aid: str) -> dict:
    return {
        "公告标题": title,
        "公告时间": date,
        "公告链接": f"http://www.cninfo.com.cn/new/disclosure/detail?stockCode=600519&announcementId={aid}"
        f"&orgId=gssh0600519&announcementTime={date}",
    }


def test_pick_annual_report_skips_summary_and_english():
    rows = [
        _row("贵州茅台2025年年度报告（英文版）", "2026-04-17", "11"),
        _row("贵州茅台2025年年度报告摘要", "2026-04-17", "12"),
        _row("贵州茅台2025年年度报告", "2026-04-17", "13"),
        _row("贵州茅台2024年年度报告", "2025-04-03", "14"),
    ]
    got = pick_report(rows, "annual")
    assert got == {
        "kind": "annual",
        "doc": "2025 年报",
        "period_end": "2025-12-31",
        "title": "贵州茅台2025年年度报告",
        "date": "2026-04-17",
        "year": 2025,
        "announcement_id": "13",
        "pdf_url": "http://static.cninfo.com.cn/finalpage/2026-04-17/13.PDF",
        "detail_url": rows[2]["公告链接"],
    }


def test_pick_annual_report_prefers_latest_revision_of_same_year():
    rows = [
        _row("2025年年度报告", "2026-04-15", "21"),
        _row("2025年年度报告(英文版）", "2026-06-17", "22"),
        _row("2025年年度报告（更正后）", "2026-05-10", "23"),
        _row("关于2025年年度报告的更正公告", "2026-05-10", "24"),
    ]
    assert pick_report(rows, "annual")["announcement_id"] == "23"


def test_pick_annual_report_none_when_no_full_report():
    assert pick_report([_row("2025年年度报告摘要", "2026-04-15", "1")], "annual") is None
    assert pick_report([], "annual") is None


def test_pick_report_interim_kinds():
    semi = [_row("金徽酒股份有限公司2026年半年度报告摘要", "2026-08-20", "31"),
            _row("金徽酒股份有限公司2026年半年度报告", "2026-08-20", "32")]
    got = pick_report(semi, "semi")
    assert (got["announcement_id"], got["doc"], got["period_end"]) == ("32", "2026 半年报", "2026-06-30")
    q = [_row("贵州茅台2026年第一季度报告", "2026-04-25", "41"), _row("贵州茅台2025年第一季度报告", "2025-04-30", "42")]
    got = pick_report(q, "q1")
    assert (got["announcement_id"], got["doc"], got["period_end"]) == ("41", "2026 一季报", "2026-03-31")
    got = pick_report([_row("2025年第三季度报告", "2025-10-25", "51")], "q3")
    assert (got["doc"], got["period_end"]) == ("2025 三季报", "2025-09-30")
    # 年报规则不能把半年报认成年报
    assert pick_report(semi, "annual") is None


# ---------------------------------------------------------------- 选哪几份报告


def _meta(kind: str, period_end: str) -> dict:
    return {"kind": kind, "period_end": period_end}


def test_choose_reports_by_calendar():
    a25 = _meta("annual", "2025-12-31")
    # 4–8 月：年报 + 一季报
    assert choose_reports({"annual": a25, "semi": _meta("semi", "2025-06-30"),
                           "q1": _meta("q1", "2026-03-31"), "q3": _meta("q3", "2025-09-30")}) == ["q1", "annual"]
    # 8–10 月：年报 + 半年报
    assert choose_reports({"annual": a25, "semi": _meta("semi", "2026-06-30"),
                           "q1": _meta("q1", "2026-03-31"), "q3": _meta("q3", "2025-09-30")}) == ["semi", "annual"]
    # 10 月 – 次年 4 月：年报 + 半年报 + 三季报（三季报原因表太简略，半年报保留）
    assert choose_reports({"annual": a25, "semi": _meta("semi", "2026-06-30"),
                           "q1": _meta("q1", "2026-03-31"), "q3": _meta("q3", "2026-09-30")}) == ["q3", "semi", "annual"]
    # 新年报发布后：只读年报
    assert choose_reports({"annual": _meta("annual", "2026-12-31"), "semi": _meta("semi", "2026-06-30"),
                           "q1": None, "q3": _meta("q3", "2026-09-30")}) == ["annual"]
    # 年报缺失（如新股）：照样读中期报告
    assert choose_reports({"annual": None, "semi": _meta("semi", "2026-06-30"), "q1": None, "q3": None}) == ["semi"]
    assert choose_reports({"annual": None, "semi": None, "q1": None, "q3": None}) == []


# ---------------------------------------------------------------- 切 MD&A


def test_extract_mdna_skips_toc_and_stops_at_section_four(mt, lx):
    # 茅台：目录在第 4 页，正文第三节从第 8 页开始，第四节在第 23 页
    assert mt[0][0] == 8 and mt[-1][0] == 23
    assert mt[0][1].lstrip().startswith("一、")
    assert "公司治理相关情况说明" not in mt[-1][1]
    assert "可能面对的风险" in mt[-1][1]
    # 立讯：第 11 页起，第 57 页止
    assert lx[0][0] == 11 and lx[-1][0] == 57
    assert "公司治理的基本状况" not in lx[-1][1]


def test_extract_mdna_strips_page_furniture(mt, lx):
    text = "\n".join(t for _, t in mt)
    assert "贵州茅台酒股份有限公司2025 年年度报告" not in text  # 页眉
    assert "/ 143" not in text  # 页脚页码
    for _, t in mt + lx:
        for line in t.splitlines():
            assert not ("适用" in line and "不适用" in line and len(line.strip()) <= 12), line


def test_extract_mdna_none_when_section_missing():
    assert extract_mdna(["第一节 重要提示", "随便一页"]) is None
    assert extract_mdna([]) is None


# ---------------------------------------------------------------- 切块


def test_split_blocks_shanghai_format(mt):
    blocks = {b["key"]: b for b in split_blocks(mt)}
    assert {"business", "industry", "discussion", "core", "main_biz", "industry_ops",
            "landscape", "strategy", "plan", "risks"} <= set(blocks)
    assert blocks["discussion"]["page"] == 8
    assert blocks["risks"]["page"] == 23
    # 块在下一个已识别标题处截断：风险块不含「（五）其他」之后的内容
    assert "宏观经济风险" in blocks["risks"]["text"]
    assert "不适用准则规定" not in blocks["risks"]["text"]
    # 「行业经营性信息分析」不吞并后面的「投资状况分析」
    assert "投资状况分析" not in blocks["industry_ops"]["text"]


def test_split_blocks_shenzhen_format(lx):
    blocks = {b["key"]: b for b in split_blocks(lx)}
    assert {"business", "industry", "core", "main_biz", "outlook"} <= set(blocks)
    # 业务块内的同级小标题（一、消费电子业务）不当作边界
    assert "消费电子业务" in blocks["business"]["text"]
    assert "汽车业务" in blocks["business"]["text"]
    # 展望块止于「接待调研」
    assert "接待调研" not in blocks["outlook"]["text"]
    assert blocks["outlook"]["page"] == 52


def test_select_blocks_respects_budget_and_priority(lx):
    picked = select_blocks(split_blocks(lx), budget=12000, block_cap=5000)
    assert sum(len(b["text"]) for b in picked) <= 12000
    keys = [b["key"] for b in picked]
    assert "outlook" in keys  # 展望 / 风险优先于主营业务分析
    # 保持原文顺序
    order = [b["page"] for b in picked]
    assert order == sorted(order)
    assert any(b.get("truncated") for b in picked) or all(len(b["text"]) <= 5000 for b in picked)


# ---------------------------------------------------------------- 原文引用校验


def test_verify_extract_keeps_real_quotes_and_drops_fabricated(mt):
    raw = {
        "drivers": [
            # pypdf 会在句中断行，LLM 给出的引用不含换行，仍应匹配
            {"topic": "revenue", "claim": "公司坚持高质量发展", "quote": mt[0][1][200:260].replace("\n", "")},
            {"topic": "margin", "claim": "瞎编", "quote": "公司毛利率大幅提升至百分之九十九"},
            {"topic": "unknown_topic", "claim": "未知 topic 归 other", "quote": mt[1][1][100:150]},
        ],
        "kpis": [],
        "risks": [{"title": "宏观经济风险", "quote": "一是宏观经济风险"}],
    }
    got = verify_extract(raw, mt)
    assert [d["claim"] for d in got["drivers"]] == ["公司坚持高质量发展", "未知 topic 归 other"]
    assert got["drivers"][0]["page"] == 8
    assert got["drivers"][1]["topic"] == "other"
    assert got["risks"][0]["page"] == 23
    assert got["dropped"] == 1


def test_verify_extract_number_rules():
    pages = [(10, "报告期内，茅台酒销量为 4.2 万吨，同比增长 5.10%。\n系列酒营收 200 亿元。")]
    raw = {
        "drivers": [
            {"topic": "revenue", "claim": "销量同比增长 5.10%", "quote": "茅台酒销量为4.2万吨，同比增长5.10%"},
            # claim 里的数字不在 quote 里 → 不丢条目，claim 退回为已校验的原文
            {"topic": "revenue", "claim": "销量同比增长 8%", "quote": "茅台酒销量为4.2万吨，同比增长5.10%"},
        ],
        "kpis": [
            {"name": "茅台酒销量", "value": "4.2", "unit": "万吨", "quote": "茅台酒销量为 4.2 万吨"},
            # value 不在 quote 里 → 丢弃
            {"name": "系列酒营收", "value": "300", "unit": "亿元", "quote": "系列酒营收 200 亿元"},
        ],
        "risks": [],
    }
    got = verify_extract(raw, pages)
    assert [d["claim"] for d in got["drivers"]] == ["销量同比增长 5.10%", "茅台酒销量为4.2万吨，同比增长5.10%"]
    assert [k["name"] for k in got["kpis"]] == ["茅台酒销量"]
    assert got["kpis"][0]["page"] == 10
    assert got["dropped"] == 1


def test_verify_extract_keeps_reason_line_when_claim_borrows_table_number():
    # 金徽酒 2025 年报：变动比例在表格行，「变动原因说明」那句不带数字；LLM 概括时把表里的 -3.40% 拼进 claim。
    # 旧规则整条丢弃，导致 9 条公司解释全军覆没——现在 claim 退回原文。
    pages = [(17, "营业收入 2,918,472,595.78 3,021,264,528.01 -3.40\n"
                  "营业收入变动原因说明：主要是受白酒行业政策管控以及行业深度调整影响，导致销量下降。")]
    raw = {"drivers": [{"topic": "revenue", "claim": "营业收入下降3.40%，主要受行业政策管控影响",
                        "quote": "营业收入变动原因说明：主要是受白酒行业政策管控以及行业深度调整影响，导致销量下降。"}],
           "kpis": [], "risks": []}
    got = verify_extract(raw, pages)
    assert got["dropped"] == 0
    assert got["drivers"][0]["claim"] == "营业收入变动原因说明：主要是受白酒行业政策管控以及行业深度调整影响，导致销量下降。"


def test_verify_extract_normalizes_width_and_rejects_short_quotes():
    pages = [(3, "营业收入同比增长（含税）１２％，主要系产品结构优化")]
    raw = {
        "drivers": [
            {"topic": "revenue", "claim": "结构优化", "quote": "营业收入同比增长(含税)12%,主要系产品结构优化"},
            {"topic": "revenue", "claim": "太短", "quote": "收入"},
        ],
        "kpis": [],
        "risks": [],
    }
    got = verify_extract(raw, pages)
    assert [d["claim"] for d in got["drivers"]] == ["结构优化"]
    assert got["dropped"] == 1


def test_verify_extract_tolerates_malformed_payload():
    got = verify_extract({"drivers": "oops", "kpis": [None, 3], "risks": [{"title": "x"}]}, [(1, "正文")])
    assert got == {"drivers": [], "kpis": [], "risks": [], "dropped": 3}


def test_locate_page_spanning_page_break():
    pages = [(5, "第一页末尾的句子开头"), (6, "延续到第二页的结尾。")]
    assert locate_page("句子开头延续到第二页", pages) == 5
    assert locate_page("不存在的句子内容", pages) is None


def test_read_until_mdna_end_stops_at_section_four():
    pages = load_pages("600519")
    consumed: list[int] = []

    def gen():
        for i, p in enumerate(pages + ["第五节 重要事项"] * 50):
            consumed.append(i)
            yield p

    got = read_until_mdna_end(gen())
    assert len(got) == 23 and len(consumed) == 23
    assert extract_mdna(got) == extract_mdna(pages)


def test_verify_extract_adjacent_table_numbers_do_not_merge():
    pages = [(15, "成品酒 半成品酒（含基础酒）\n24,770.35 315,207.51")]
    raw = {"drivers": [], "risks": [],
           "kpis": [{"name": "成品酒库存", "value": "24,770.35", "unit": "吨", "quote": "24,770.35 315,207.51"}]}
    assert verify_extract(raw, pages)["kpis"][0]["page"] == 15



# ---------------------------------------------------------------- 半年报 / 季报


def test_split_blocks_semi_report_format():
    mdna = extract_mdna(load_pages("603919", "semi_{code}_2026"))
    assert mdna is not None and mdna[0][0] == 10
    blocks = {b["key"]: b for b in split_blocks(mdna)}
    # 半年报写「经营情况的讨论与分析」「所属行业及主营业务情况说明」
    assert "营业收入 18.16亿元" in blocks["discussion"]["text"]
    assert "business" in blocks
    # 利润下滑原因写在「（二）其他披露事项」的质量回报行动方案执行情况里；风险块不再吞并它
    assert "补缴税款" in blocks["disclosure"]["text"]
    assert "补缴税款" not in blocks["risks"]["text"]
    # 资产负债情况分析是独立块：应收账款大增的原因在「注 2」
    assert "应收账款变动主要原因" in blocks["balance"]["text"]
    assert "投资状况分析" not in blocks["balance"]["text"]
    assert "市场竞争风险" in blocks["risks"]["text"]


def test_extract_quarterly_change_reasons():
    sec = extract_quarterly(load_pages("603919", "q1_{code}_2026"))
    assert sec is not None
    text = "\n".join(t for _, t in sec)
    assert "消费场景受限" in text and "销售回款较上期同比增加" in text
    assert "股东信息" not in text and "甘肃亚特投资" not in text
    assert "2026 年第一季度报告" not in text  # 页眉
    # 茅台季报还带「销售情况」「经销商情况」
    sec = extract_quarterly(load_pages("600519", "q1_{code}_2026"))
    text = "\n".join(t for _, t in sec)
    assert "同业存款" in text and "销售情况" in text and "经销商情况" in text
    assert "贵州茅台酒股份有限公司2026 年第一季度报告" not in text
    assert extract_quarterly(["随便一页", "二、股东信息"]) is None
