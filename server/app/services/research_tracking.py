"""后续跟踪清单：纯代码生成（不经 LLM），告诉读者接下来盯什么、为什么盯、公司怎么解释的。

来源（按展示顺序）：排雷项 → 指标（异常规则 + 得分偏低）→ 需跟进的负面公告 → 公司自述风险 → 下一期定期报告。
指标若在定期报告深读里有公司解释（关键词命中），附上最新一条的出处。
"""

from __future__ import annotations

from collections.abc import Callable

from app.services.research_facts import fmt

KIND_ORDER = ["flag", "metric", "event", "risk", "next"]

# (fact_id, 触发条件, 说明)——阈值是「值得跟踪」，比评分的零分点宽松
ANOMALIES: list[tuple[str, Callable[[float], bool], str]] = [
    ("deducted_np_yoy", lambda v: v < -10, "扣非净利润同比下滑"),
    ("revenue_yoy", lambda v: v < 0, "营收同比下滑"),
    ("rev_q_yoy_latest", lambda v: v < -10, "最近单季营收同比明显下滑"),
    ("ar_yoy", lambda v: v > 30, "应收账款增长较快，关注回款与信用政策"),
    ("contract_liab_yoy", lambda v: v < -15, "合同负债（预收款）同比下降，可能预示后续收入放缓"),
    ("ocf_to_np_3y", lambda v: v < 0.8, "经营现金流对利润覆盖不足"),
    ("debt_ratio", lambda v: v > 65, "资产负债率偏高"),
    ("pe_pct_self", lambda v: v > 80, "估值处于自身历史高位"),
]
WEAK_RATIO = 0.3  # 得分率低于此视为偏低
PROGRESS_LAG_PP = 10  # 预测完成度比去年同期低这么多个百分点才提示

# 指标 → 在公司解释里找的关键词
EXPLAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "deducted_np_yoy": ("净利润",), "deducted_np_cagr_3y": ("净利润",), "net_margin": ("净利润", "费用"),
    "revenue_yoy": ("营业收入",), "rev_q_yoy_latest": ("营业收入",), "rev_q_yoy_change": ("营业收入",),
    "revenue_cagr_3y": ("营业收入",), "ar_yoy": ("应收账款",), "ar_minus_rev_growth": ("应收账款",),
    "contract_liab_yoy": ("合同负债", "预收"), "ocf_to_np_3y": ("经营活动",), "fcf_to_np_3y": ("投资活动", "在建工程"),
    "gross_margin": ("营业成本", "毛利"), "debt_ratio": ("借款",),
}

NEGATIVE_EVENTS = ("investigation", "audit_nonstandard", "inquiry", "reduce", "forecast_down", "lawsuit")

# 报告期 → (下一期报告, 法定披露截止)
_NEXT_REPORT = {
    "03-31": lambda y: (f"{y} 半年报", f"{y}-08-31"),
    "06-30": lambda y: (f"{y} 三季报", f"{y}-10-31"),
    "09-30": lambda y: (f"{y} 年报", f"{y + 1}-04-30"),
    "12-31": lambda y: (f"{y + 1} 一季报", f"{y + 1}-04-30"),
}


def _explain(fid: str, drivers: list[dict]) -> dict | None:
    for kw in EXPLAIN_KEYWORDS.get(fid, ()):
        for d in drivers:  # 已按报告期由新到旧
            if kw in d["claim"]:
                return {"claim": d["claim"], "doc": d.get("doc") or "年报", "page": d["page"]}
    return None


def build_tracking(dataset: dict, result: dict, periodic: dict | None) -> list[dict]:
    """返回 [{kind, key, label, value, note, explain?}]，按 KIND_ORDER 分组。"""
    facts = dataset["facts"]
    drivers = (periodic or {}).get("drivers") or []
    items: list[dict] = []

    for f in result["red_flags"]:
        items.append({"kind": "flag", "key": f"flag_{f['key']}", "label": f["label"], "value": "", "note": f["detail"]})

    seen: set[str] = set()
    for fid, cond, note in ANOMALIES:
        f = facts.get(fid)
        if f is not None and cond(f["value"]):
            seen.add(fid)
            items.append({"kind": "metric", "key": fid, "label": f["label"], "value": fmt(f), "note": note,
                          "explain": _explain(fid, drivers)})
    for key, d in result["sub_scores"].items():
        if key == "events":
            continue
        for it in d["items"]:
            if it["key"] in seen or it["points"] is None or not it["max"] or it["points"] / it["max"] >= WEAK_RATIO:
                continue
            seen.add(it["key"])
            items.append({"kind": "metric", "key": it["key"], "label": it["label"], "value": it["display"],
                          "note": f"得分偏低（{it['points']} / {it['max']}）", "explain": _explain(it["key"], drivers)})

    # 预测完成度明显慢于去年同期（季节性基准）：全年一致预期能否兑现
    prog, prev = facts.get("inst_eps_progress"), facts.get("eps_progress_prev")
    if prog and prev and prog["value"] < prev["value"] - PROGRESS_LAG_PP:
        items.append({"kind": "metric", "key": "inst_eps_progress", "label": prog["label"], "value": fmt(prog),
                      "note": f"慢于去年同期（{fmt(prev)}），关注全年机构预测能否兑现",
                      "explain": _explain("deducted_np_yoy", drivers)})

    latest_by_tag: dict[str, dict] = {}
    for e in dataset.get("events") or []:  # 已按日期倒序
        if e["tag"] in NEGATIVE_EVENTS and e["tag"] not in latest_by_tag:
            latest_by_tag[e["tag"]] = e
    for tag in NEGATIVE_EVENTS:
        if (e := latest_by_tag.get(tag)) is not None:
            items.append({"kind": "event", "key": f"event_{tag}", "label": e["label"], "value": e["date"] or "",
                          "note": e["title"], "url": e.get("url")})

    for r in (periodic or {}).get("risks") or []:
        items.append({"kind": "risk", "key": f"risk_{r['title']}", "label": r["title"], "value": "",
                      "note": f"公司自述（{r.get('doc') or '年报'} 第 {r['page']} 页）"})

    as_of = dataset.get("data_as_of") or ""
    nxt = _NEXT_REPORT.get(as_of[5:])
    if nxt:
        label, deadline = nxt(int(as_of[:4]))
        items.append({"kind": "next", "key": "next", "label": label, "value": "",
                      "note": f"法定披露截止 {deadline}；届时以上指标与公司解释随新报告更新"})
    return items
