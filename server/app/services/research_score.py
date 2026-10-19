"""基本面评分 v1.1（docs/RESEARCH-DESIGN.md §3）——纯函数，同输入永远同输出。

每个指标分段线性：零分点 → 满分点之间插值，两端截断（零分点 > 满分点即反向指标）。
缺失指标不计分；维度可用满分 < 50% 时该维度「数据不足」不进总分；
整体覆盖率 < 70% 不给评级；命中排雷项评级封顶 C，ST / 立案调查直接定 D（总分不改）。
业绩恶化导致比值无定义（扣非转亏、累计亏损）按规则给分，不当缺失——否则维度归一会替它免掉扣分。
"""

from __future__ import annotations

from app.services.research_facts import EVENT_LABELS, fmt

SCORING_VERSION = "v1.1"

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
GRADE_D_FLAGS = {"st", "investigation"}  # 命中直接定 D；其余排雷项封顶 C
SELF_HIST_MIN_DAYS = 480  # 自身历史不足约 2 年时，自身分位权重一半移给同行业分位
MIN_COVERAGE = 0.7
DIM_MIN_AVAILABLE = 0.5

DIMENSION_LABELS = {k: label for k, label, _, _ in DIMENSIONS} | {"events": "事件"}


def linear(value: float, max_points: float, zero: float, full: float) -> float:
    t = (value - zero) / (full - zero)
    return max_points * min(1.0, max(0.0, t))


def _item_points(fid: str, facts: dict, max_pts: float, zero: float, full: float) -> tuple[float | None, str | None]:
    """返回 (得分 | None=缺失, 说明)。"""
    def v(key: str) -> float | None:
        return facts.get(key, {}).get("value")

    pe = v("pe")
    if fid in ("pe_pct_self", "pe_pct_industry") and pe is not None and pe <= 0:
        return 0.0, "亏损（市盈率 ≤ 0）记 0 分"
    if fid == "ar_minus_rev_growth":
        ratio = v("ar_to_revenue")
        if ratio is not None and ratio < 2:
            return max_pts, "应收账款占营收 < 2%，视为无应收风险"
    f = facts.get(fid)
    if f is not None:
        return linear(f["value"], max_pts, zero, full), None
    if fid == "deducted_np_cagr_3y":
        end = v("deducted_np_cagr_end")
        if end is not None and end <= 0:
            return 0.0, "最近年报扣非净利润 ≤ 0，复合增速无定义，记 0 分"
    if fid in ("ocf_to_np_3y", "fcf_to_np_3y") and v("np_3y") is not None:
        cash = v("ocf_3y") if fid == "ocf_to_np_3y" else v("fcf_3y")
        if cash is not None and cash > 0:
            return max_pts / 2, "累计净利润 ≤ 0，比值无意义；累计现金流为正记一半分"
        if cash is not None:
            return 0.0, "累计净利润 ≤ 0 且累计现金流 ≤ 0，记 0 分"
    return None, None


def _weights(key: str, items: list, facts: dict) -> tuple[list, dict[str, str]]:
    """自身估值历史过短时，把自身分位的一半权重移给同行业分位。返回 (items, {fid: 说明})。"""
    days = facts.get("pe_pct_self", {}).get("days")
    if key != "valuation" or days is None or days >= SELF_HIST_MIN_DAYS or "pe_pct_industry" not in facts:
        return items, {}
    note = f"自身历史仅 {days} 个交易日（< {SELF_HIST_MIN_DAYS}），一半权重移至同行业分位"
    moved = sum(m / 2 for fid, m, _, _ in items if fid in ("pe_pct_self", "pb_pct_self"))
    out = []
    for fid, m, zero, full in items:
        if fid in ("pe_pct_self", "pb_pct_self"):
            m = m / 2
        elif fid == "pe_pct_industry":
            m = m + moved
        out.append((fid, m, zero, full))
    return out, {"pe_pct_self": note, "pb_pct_self": note, "pe_pct_industry": note}


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
        items, weight_notes = _weights(key, items, facts)
        for fid, max_pts, zero, full in items:
            pts, note = _item_points(fid, facts, max_pts, zero, full)
            if pts is not None and fid in weight_notes:
                note = "；".join(x for x in (note, weight_notes[fid]) if x)
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
        if any(f["key"] in GRADE_D_FLAGS for f in flags):
            grade = "D"
        elif flags and grade in ("A", "B"):
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
