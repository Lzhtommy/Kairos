"""基本面评分 v1（docs/RESEARCH-DESIGN.md §3）——纯函数，同输入永远同输出。

每个指标分段线性：零分点 → 满分点之间插值，两端截断（零分点 > 满分点即反向指标）。
缺失指标不计分；维度可用满分 < 50% 时该维度「数据不足」不进总分；
整体覆盖率 < 70% 不给评级；命中排雷项评级封顶 C（总分不改）。
"""

from __future__ import annotations

from app.services.research_facts import EVENT_LABELS, fmt

SCORING_VERSION = "v1"

# (维度 key, 维度名, 满分, [(fact_id, 满分, 零分点, 满分点)])
DIMENSIONS: list[tuple[str, str, float, list[tuple[str, float, float, float]]]] = [
    ("profitability", "盈利能力", 20, [
        ("roe", 8, 5, 20),
        ("gross_margin", 4, 15, 50),
        ("net_margin", 4, 3, 20),
        ("roe_std_5y", 4, 10, 2),
    ]),
    ("growth", "成长性", 20, [
        ("revenue_cagr_3y", 6, 0, 20),
        ("deducted_np_cagr_3y", 6, 0, 25),
        ("deducted_np_yoy", 5, -20, 30),
        ("rev_q_yoy_change", 3, -10, 10),
    ]),
    ("health", "财务健康", 15, [
        ("debt_ratio", 6, 70, 30),
        ("current_ratio", 4, 1.0, 2.0),
        ("interest_debt_to_cash", 5, 1.5, 0.3),
    ]),
    ("cashflow", "现金流质量", 15, [
        ("ocf_to_np_3y", 7, 0.5, 1.2),
        ("fcf_to_np_3y", 4, 0, 0.5),
        ("ar_minus_rev_growth", 4, 30, 0),
    ]),
    ("valuation", "估值", 15, [
        ("pe_pct_self", 5, 90, 10),
        ("pb_pct_self", 3, 90, 10),
        ("pe_pct_industry", 7, 90, 10),
    ]),
    ("shareholder", "股东回报", 5, [
        ("dividend_yield", 3, 0, 4),
        ("payout_3y", 2, 10, 50),
    ]),
]

EVENTS_MAX = 10.0
EVENTS_BASE = 5.0
EVENT_POINTS = {
    "buyback": 2, "increase": 2, "incentive": 1, "forecast_up": 1, "major_contract": 1,
    "reduce": -2, "inquiry": -2, "investigation": -5, "forecast_down": -2, "lawsuit": -1,
}

GRADES = [(80, "A"), (65, "B"), (50, "C"), (float("-inf"), "D")]
MIN_COVERAGE = 0.7
DIM_MIN_AVAILABLE = 0.5

DIMENSION_LABELS = {k: label for k, label, _, _ in DIMENSIONS} | {"events": "事件"}


def linear(value: float, max_points: float, zero: float, full: float) -> float:
    t = (value - zero) / (full - zero)
    return max_points * min(1.0, max(0.0, t))


def _item_points(fid: str, facts: dict, max_pts: float, zero: float, full: float) -> tuple[float | None, str | None]:
    """返回 (得分 | None=缺失, 说明)。"""
    pe = facts.get("pe", {}).get("value")
    if fid in ("pe_pct_self", "pe_pct_industry") and pe is not None and pe <= 0:
        return 0.0, "亏损（市盈率 ≤ 0）记 0 分"
    if fid == "ar_minus_rev_growth":
        ratio = facts.get("ar_to_revenue", {}).get("value")
        if ratio is not None and ratio < 2:
            return max_pts, "应收账款占营收 < 2%，视为无应收风险"
    f = facts.get(fid)
    if f is None:
        return None, None
    return linear(f["value"], max_pts, zero, full), None


def score(dataset: dict) -> dict:
    """dataset: research_facts.build_facts 的结果。

    返回 {total_score, grade, coverage, sub_scores, red_flags, scoring_version}。
    """
    facts = dataset["facts"]
    sub: dict[str, dict] = {}
    total_avail = 0.0

    for key, label, dim_max, items in DIMENSIONS:
        got = avail = 0.0
        rows = []
        for fid, max_pts, zero, full in items:
            pts, note = _item_points(fid, facts, max_pts, zero, full)
            f = facts.get(fid)
            rows.append({
                "key": fid,
                "label": f["label"] if f else fid,
                "value": f["value"] if f else None,
                "display": fmt(f),
                "unit": f["unit"] if f else None,
                "period": f.get("period") if f else None,
                "source": f.get("source") if f else None,
                "points": None if pts is None else round(pts, 2),
                "max": max_pts,
                "zero": zero,
                "full": full,
                "note": note,
            })
            if pts is not None:
                got += pts
                avail += max_pts
        insufficient = avail < dim_max * DIM_MIN_AVAILABLE
        total_avail += avail
        sub[key] = {
            "label": label,
            "max": dim_max,
            "available": avail,
            "insufficient": insufficient,
            "score": None if insufficient or avail == 0 else round(got / avail * dim_max, 2),
            "items": rows,
        }

    # 事件分：同类标签只计一次
    events = dataset.get("events")
    if events is None:
        sub["events"] = {"label": "事件", "max": EVENTS_MAX, "available": 0, "insufficient": True,
                         "score": None, "items": []}
    else:
        tags = sorted({e["tag"] for e in events if e["tag"] in EVENT_POINTS})
        pts = EVENTS_BASE + sum(EVENT_POINTS[t] for t in tags)
        total_avail += EVENTS_MAX
        sub["events"] = {
            "label": "事件",
            "max": EVENTS_MAX,
            "available": EVENTS_MAX,
            "insufficient": False,
            "score": round(min(EVENTS_MAX, max(0.0, pts)), 2),
            "items": [
                {"key": f"event_{t}", "label": EVENT_LABELS.get(t, t), "value": None,
                 "display": f"{sum(1 for e in events if e['tag'] == t)} 条", "points": EVENT_POINTS[t],
                 "max": None, "zero": None, "full": None, "note": "同类只计一次"}
                for t in tags
            ],
        }

    scored = [d for d in sub.values() if d["score"] is not None]
    max_sum = sum(d["max"] for d in scored)
    total = round(sum(d["score"] for d in scored) / max_sum * 100, 1) if max_sum else None
    coverage = round(total_avail / 100, 3)

    flags = red_flags(dataset)
    grade = None
    if total is not None and coverage >= MIN_COVERAGE:
        grade = next(g for th, g in GRADES if total >= th)
        if flags and grade in ("A", "B"):
            grade = "C"

    return {
        "scoring_version": SCORING_VERSION,
        "total_score": total,
        "grade": grade,
        "coverage": coverage,
        "sub_scores": sub,
        "red_flags": flags,
    }


def red_flags(dataset: dict) -> list[dict]:
    facts = dataset["facts"]
    out: list[dict] = []

    def v(fid: str) -> float | None:
        return facts.get(fid, {}).get("value")

    if dataset.get("is_st"):
        out.append({"key": "st", "label": "ST / *ST", "detail": "证券简称含 ST，存在退市风险警示或其他风险警示"})
    gw = v("goodwill_to_equity")
    if gw is not None and gw > 30:
        out.append({"key": "goodwill", "label": "商誉过高", "detail": f"商誉 / 归母净资产 {fmt(facts['goodwill_to_equity'])}（> 30%）"})
    ca, da = v("cash_to_assets"), v("interest_debt_to_assets")
    if ca is not None and da is not None and ca > 20 and da > 20:
        out.append({
            "key": "cash_debt_double_high", "label": "存贷双高",
            "detail": f"货币资金 / 总资产 {fmt(facts['cash_to_assets'])}，有息负债 / 总资产 {fmt(facts['interest_debt_to_assets'])}（均 > 20%）",
        })
    last2 = [x for x in dataset.get("deducted_last2") or [] if x is not None]
    if len(last2) == 2 and all(x < 0 for x in last2):
        out.append({"key": "losses", "label": "持续亏损", "detail": "最近两个完整年度扣非净利润均为负"})
    for e in dataset.get("events") or []:
        if e["tag"] == "investigation":
            out.append({"key": "investigation", "label": "立案调查", "detail": f"{e['date']} {e['title']}"})
            break
    for e in dataset.get("events") or []:
        if e["tag"] == "audit_nonstandard":
            out.append({"key": "audit", "label": "非标审计意见", "detail": f"{e['date']} {e['title']}"})
            break
    return out
