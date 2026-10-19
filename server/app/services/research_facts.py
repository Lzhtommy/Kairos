"""研报事实表：原始 records → facts（纯函数，不碰网络 / 数据库，可单测）。

facts 是报告里所有数字的唯一来源：{fact_id: {value, unit, label, source, period, group}}。
- 评分只读 facts（research_score）；LLM 只能引用 facts（research_writer 做数字校验）。
- 金额统一为「亿元」，比例统一为百分数（34.19 表示 34.19%）。
- 财报是累计值：增速用累计同比，年度指标用完整年报，单季值由累计值差分得到。
"""

from __future__ import annotations

import math
import re
import statistics
from datetime import date
from typing import Any

# 金融类不适用 v1 评分规则（报表结构不同）
FINANCIAL_INDUSTRIES_SW = {"证券", "保险", "多元金融", "城商行", "农商行", "股份制银行", "国有大型银行"}
FINANCIAL_INDUSTRIES_CSRC = {"货币金融服务", "资本市场服务", "保险业", "其他金融业"}


def is_financial(industry: str | None, csrc_industry: str | None = None) -> bool:
    return (industry or "") in FINANCIAL_INDUSTRIES_SW or (csrc_industry or "") in FINANCIAL_INDUSTRIES_CSRC


# ---------------------------------------------------------------- 解析工具


def num(v: Any) -> float | None:
    """数值解析：None / NaN / '--' / '' → None；同花顺的 '272.43亿' '1.2万' '1.47%' 也能解。

    带「亿 / 万」后缀的返回亿元；带 % 的返回百分数本身；纯数字原样返回。
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return None if math.isnan(v) or math.isinf(v) else float(v)
    s = str(v).strip().replace(",", "")
    if s in ("", "--", "-", "False", "None", "nan"):
        return None
    scale = 1.0
    if s.endswith("%"):
        s = s[:-1]
    elif s.endswith("万亿"):
        s, scale = s[:-2], 1e4
    elif s.endswith("亿"):
        s = s[:-1]
    elif s.endswith("万"):
        s, scale = s[:-1], 1e-4
    try:
        out = float(s) * scale
    except ValueError:
        return None
    return None if math.isnan(out) or math.isinf(out) else out


def yuan_to_yi(v: Any) -> float | None:
    x = num(v)
    return None if x is None else x / 1e8


def norm_date(v: Any) -> str | None:
    """'20260630' / '2026-06-30' / '2026-06-30T00:00:00.000' → '2026-06-30'。"""
    if v is None:
        return None
    s = re.sub(r"[^0-9]", "", str(v))[:8]
    if len(s) != 8:
        return None
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"


def _year_ago(d: str) -> str:
    return f"{int(d[:4]) - 1}{d[4:]}"


def _pct_rank(values: list[float], current: float) -> float:
    """当前值在样本中的分位（%）：严格小于当前值的占比。"""
    below = sum(1 for v in values if v < current)
    return below / len(values) * 100


def _cagr(start: float | None, end: float | None, years: int) -> float | None:
    if start is None or end is None or start <= 0 or end <= 0:
        return None
    return ((end / start) ** (1 / years) - 1) * 100


# ---------------------------------------------------------------- 格式化

UNIT_FMT = {
    "%": lambda v: f"{v:.2f}%",
    "亿元": lambda v: f"{v:.2f}亿元",
    "倍": lambda v: f"{v:.2f}倍",
    "pp": lambda v: f"{v:.2f}个百分点",
    "元": lambda v: f"{v:.2f}元",
    "篇": lambda v: f"{v:.0f}篇",
    "": lambda v: f"{v:.2f}",
}


def fmt(fact: dict | None) -> str:
    if not fact or fact.get("value") is None:
        return "—"
    return UNIT_FMT.get(fact.get("unit", ""), UNIT_FMT[""])(fact["value"])


# ---------------------------------------------------------------- 公告事件（关键词规则，确定性）

# 顺序即优先级；exclude 命中则跳过该规则。事件分进总分，必须可复现——所以用规则而不是 LLM。
EVENT_RULES: list[tuple[str, str, str, str | None]] = [
    ("investigation", "立案调查", r"立案", None),
    ("audit_nonstandard", "非标审计意见", r"非标准审计意见|保留意见|无法表示意见|否定意见", None),
    ("inquiry", "问询 / 监管函", r"问询函|监管函|关注函|警示函|监管工作函", None),
    ("forecast_down", "业绩预减 / 亏损", r"预减|预亏|首亏|续亏|业绩大幅下降", None),
    ("forecast_up", "业绩预增", r"预增|扭亏|预盈|业绩大幅增长", None),
    ("incentive", "股权激励", r"股权激励|限制性股票激励|股票期权激励|员工持股计划", r"回购注销|作废|注销|终止"),
    ("buyback", "股份回购", r"回购.*(股份|股票)|(股份|股票).*回购", r"回购注销|注销.*限制性|限制性股票|业绩补偿|终止"),
    ("increase", "股东增持", r"增持", r"减持"),
    ("reduce", "股东减持", r"减持", r"终止|不减持|承诺不|期限届满未"),
    ("lawsuit", "重大诉讼", r"诉讼|仲裁", None),
    ("major_contract", "重大合同 / 中标", r"重大合同|中标", None),
]
EVENT_LABELS = {k: label for k, label, _, _ in EVENT_RULES}


def classify_notice(title: str) -> str:
    for key, _label, pattern, exclude in EVENT_RULES:
        if re.search(pattern, title) and not (exclude and re.search(exclude, title)):
            return key
    return "other"


# ---------------------------------------------------------------- 主入口


def build_facts(raw: dict[str, list[dict] | None], valuation: dict, name: str = "") -> dict:
    """raw: research_data.fetch_all 的结果；valuation: 来自本地行情库（见 research.valuation_inputs）。

    返回 {facts, data_as_of, profile, segments, events, news, consensus, is_st}。
    """
    facts: dict[str, dict] = {}

    def put(fid: str, value: float | None, unit: str, label: str, source: str, period: str | None, group: str):
        if value is None or math.isnan(value) or math.isinf(value):
            return
        facts[fid] = {
            "value": round(value, 4), "unit": unit, "label": label,
            "source": source, "period": period, "group": group,
        }

    # ---------- 年度摘要（同花顺）
    annual = []
    for r in raw.get("abstract_annual") or []:
        y = str(r.get("报告期") or "").strip()
        if re.fullmatch(r"\d{4}", y):
            annual.append({**r, "_y": int(y)})
    annual.sort(key=lambda r: r["_y"])
    annual = annual[-6:]
    last_y = annual[-1]["_y"] if annual else None
    by_year = {r["_y"]: r for r in annual}

    roes = []
    for r in annual[-5:]:
        roe = num(r.get("净资产收益率"))
        put(f"roe_{r['_y']}", roe, "%", f"{r['_y']} 年 ROE", "abstract_annual", str(r["_y"]), "profitability")
        if roe is not None:
            roes.append(roe)
    if last_y is not None:
        last = by_year[last_y]
        put("roe", num(last.get("净资产收益率")), "%", "最近年报 ROE", "abstract_annual", str(last_y), "profitability")
        put("gross_margin", num(last.get("销售毛利率")), "%", "销售毛利率", "abstract_annual", str(last_y), "profitability")
        put("net_margin", num(last.get("销售净利率")), "%", "销售净利率", "abstract_annual", str(last_y), "profitability")
    if len(roes) >= 3:
        mean = sum(roes) / len(roes)
        std = math.sqrt(sum((x - mean) ** 2 for x in roes) / len(roes))
        put("roe_std_5y", std, "pp", f"近 {len(roes)} 年 ROE 标准差", "abstract_annual", None, "profitability")

    for r in annual[-4:]:
        y = r["_y"]
        put(f"revenue_{y}", num(r.get("营业总收入")), "亿元", f"{y} 年营业总收入", "abstract_annual", str(y), "growth")
        put(f"deducted_np_{y}", num(r.get("扣非净利润")), "亿元", f"{y} 年扣非净利润", "abstract_annual", str(y), "growth")
    if last_y is not None and (last_y - 3) in by_year:
        start, end = by_year[last_y - 3], by_year[last_y]
        put("revenue_cagr_3y", _cagr(num(start.get("营业总收入")), num(end.get("营业总收入")), 3),
            "%", "营收 3 年复合增速", "abstract_annual", f"{last_y - 3}-{last_y}", "growth")
        put("deducted_np_cagr_3y", _cagr(num(start.get("扣非净利润")), num(end.get("扣非净利润")), 3),
            "%", "扣非净利润 3 年复合增速", "abstract_annual", f"{last_y - 3}-{last_y}", "growth")

    # ---------- 报告期摘要（同花顺）：最新累计同比
    periodic = sorted(
        (r for r in raw.get("abstract_report") or [] if norm_date(r.get("报告期"))),
        key=lambda r: norm_date(r.get("报告期")),
    )
    if periodic:
        lp = periodic[-1]
        d = norm_date(lp.get("报告期"))
        put("deducted_np_yoy", num(lp.get("扣非净利润同比增长率")), "%", "最新累计期扣非净利润同比",
            "abstract_report", d, "growth")
        put("revenue_yoy", num(lp.get("营业总收入同比增长率")), "%", "最新累计期营收同比", "abstract_report", d, "growth")

    # ---------- 新浪三表
    def by_date(records: list[dict] | None) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for r in records or []:
            d = norm_date(r.get("报告日"))
            if not d:
                continue
            if "类型" in r and r["类型"] and "合并" not in str(r["类型"]):
                continue
            out.setdefault(d, r)
        return out

    income = by_date(raw.get("income"))
    balance = by_date(raw.get("balance"))
    cashflow = by_date(raw.get("cashflow"))

    # 单季营收同比：累计值差分
    def cum_rev(d: str) -> float | None:
        r = income.get(d)
        return yuan_to_yi(r.get("营业收入") if r and r.get("营业收入") is not None else (r or {}).get("营业总收入"))

    quarter_ends = ("03-31", "06-30", "09-30", "12-31")

    def single_q(d: str) -> float | None:
        cur = cum_rev(d)
        if cur is None:
            return None
        idx = quarter_ends.index(d[5:]) if d[5:] in quarter_ends else -1
        if idx < 0:
            return None
        if idx == 0:
            return cur
        prev = cum_rev(f"{d[:4]}-{quarter_ends[idx - 1]}")
        return None if prev is None else cur - prev

    def sq_yoy(d: str) -> float | None:
        cur, ago = single_q(d), single_q(_year_ago(d))
        if cur is None or ago is None or ago <= 0:
            return None
        return (cur / ago - 1) * 100

    q_dates = sorted(d for d in income if d[5:] in quarter_ends)
    if len(q_dates) >= 2:
        d1, d0 = q_dates[-1], q_dates[-2]
        y1, y0 = sq_yoy(d1), sq_yoy(d0)
        put("rev_q_yoy_latest", y1, "%", "最近单季营收同比", "income", d1, "growth")
        put("rev_q_yoy_prev", y0, "%", "前一单季营收同比", "income", d0, "growth")
        if y1 is not None and y0 is not None:
            put("rev_q_yoy_change", y1 - y0, "pp", "单季营收同比变化", "income", d1, "growth")

    data_as_of = max(balance) if balance else (norm_date(periodic[-1].get("报告期")) if periodic else None)

    if balance:
        ld = max(balance)
        b = balance[ld]
        assets, liab = yuan_to_yi(b.get("资产总计")), yuan_to_yi(b.get("负债合计"))
        cur_a, cur_l = yuan_to_yi(b.get("流动资产合计")), yuan_to_yi(b.get("流动负债合计"))
        cash = yuan_to_yi(b.get("货币资金"))
        goodwill = yuan_to_yi(b.get("商誉")) or 0.0
        equity = yuan_to_yi(b.get("归属于母公司股东权益合计")) or yuan_to_yi(b.get("所有者权益(或股东权益)合计"))
        idebt = sum(
            yuan_to_yi(b.get(k)) or 0.0 for k in ("短期借款", "一年内到期的非流动负债", "长期借款", "应付债券")
        )
        put("total_assets", assets, "亿元", "总资产", "balance", ld, "health")
        put("cash", cash, "亿元", "货币资金", "balance", ld, "health")
        put("interest_debt", idebt, "亿元", "有息负债", "balance", ld, "health")
        if assets and liab is not None:
            put("debt_ratio", liab / assets * 100, "%", "资产负债率", "balance", ld, "health")
        if cur_a is not None and cur_l:
            put("current_ratio", cur_a / cur_l, "倍", "流动比率", "balance", ld, "health")
        if cash and cash > 0:
            put("interest_debt_to_cash", idebt / cash, "倍", "有息负债 / 货币资金", "balance", ld, "health")
        if equity and equity > 0:
            put("goodwill_to_equity", goodwill / equity * 100, "%", "商誉 / 归母净资产", "balance", ld, "health")
        if assets:
            if cash is not None:
                put("cash_to_assets", cash / assets * 100, "%", "货币资金 / 总资产", "balance", ld, "health")
            put("interest_debt_to_assets", idebt / assets * 100, "%", "有息负债 / 总资产", "balance", ld, "health")

        # 应收增速 vs 营收增速（累计同比）
        def receivable(r: dict | None) -> float | None:
            if not r:
                return None
            v = yuan_to_yi(r.get("应收账款"))
            return v if v is not None else yuan_to_yi(r.get("应收票据及应收账款"))

        ago = _year_ago(ld)

        # 合同负债（预收款）：预收模式行业（白酒等）的领先指标，只展示不计分。2020 年新收入准则前叫「预收款项」
        def contract_liab(r: dict | None) -> float | None:
            if not r:
                return None
            v = yuan_to_yi(r.get("合同负债"))
            return v if v is not None else yuan_to_yi(r.get("预收款项"))

        cl_now, cl_ago = contract_liab(b), contract_liab(balance.get(ago))
        put("contract_liab", cl_now, "亿元", "合同负债（预收款）", "balance", ld, "health")
        if cl_now is not None and cl_ago and cl_ago > 0:
            put("contract_liab_yoy", (cl_now / cl_ago - 1) * 100, "%", "合同负债同比", "balance", ld, "health")

        ar_now, ar_ago = receivable(b), receivable(balance.get(ago))
        rev_now, rev_ago = cum_rev(ld), cum_rev(ago)
        if ar_now is not None and rev_now and rev_now > 0:
            put("ar_to_revenue", ar_now / rev_now * 100, "%", "应收账款 / 累计营收", "balance", ld, "cashflow")
        if ar_now is not None and ar_ago and ar_ago > 0 and rev_now and rev_ago and rev_ago > 0:
            ar_g = (ar_now / ar_ago - 1) * 100
            rev_g = (rev_now / rev_ago - 1) * 100
            put("ar_yoy", ar_g, "%", "应收账款同比", "balance", ld, "cashflow")
            put("ar_minus_rev_growth", ar_g - rev_g, "pp", "应收增速 − 营收增速", "balance", ld, "cashflow")

    # 现金流：近 3 个完整年度
    years = sorted({int(d[:4]) for d in cashflow if d.endswith("12-31")})
    ocf_sum = np_sum = fcf_sum = 0.0
    n_years = 0
    for y in years[-3:]:
        d = f"{y}-12-31"
        ocf = yuan_to_yi(cashflow[d].get("经营活动产生的现金流量净额"))
        capex = yuan_to_yi(cashflow[d].get("购建固定资产、无形资产和其他长期资产所支付的现金")) or 0.0
        np_ = yuan_to_yi((income.get(d) or {}).get("净利润"))
        if ocf is None or np_ is None:
            continue
        put(f"ocf_{y}", ocf, "亿元", f"{y} 年经营现金流净额", "cashflow", str(y), "cashflow")
        put(f"np_{y}", np_, "亿元", f"{y} 年净利润", "income", str(y), "cashflow")
        ocf_sum += ocf
        np_sum += np_
        fcf_sum += ocf - capex
        n_years += 1
    if n_years >= 2 and np_sum > 0:
        period = f"{years[-3:][0]}-{years[-1]}"
        put("ocf_to_np_3y", ocf_sum / np_sum, "倍", f"近 {n_years} 年经营现金流 / 净利润", "cashflow", period, "cashflow")
        put("fcf_3y", fcf_sum, "亿元", f"近 {n_years} 年累计自由现金流", "cashflow", period, "cashflow")
        put("fcf_to_np_3y", fcf_sum / np_sum, "倍", f"近 {n_years} 年累计自由现金流 / 净利润", "cashflow", period, "cashflow")

    # ---------- 分红率：每股派息 / 基本每股收益
    dividend = raw.get("dividend")
    if dividend is not None and last_y is not None:
        dps: dict[int, float] = {}
        for r in dividend:
            m = re.match(r"(\d{4})", str(r.get("报告时间") or ""))
            per10 = num(r.get("派息比例"))
            if m and per10:
                dps[int(m.group(1))] = dps.get(int(m.group(1)), 0.0) + per10 / 10
        # 最新年报的分红通常年中才实施：未实施就往前错一年
        end_y = last_y if last_y in dps else last_y - 1
        payouts = []
        for y in range(end_y - 2, end_y + 1):
            eps = num((by_year.get(y) or {}).get("基本每股收益"))
            if eps and eps > 0:
                p = dps.get(y, 0.0) / eps * 100
                put(f"payout_{y}", p, "%", f"{y} 年分红率", "dividend", str(y), "shareholder")
                payouts.append(p)
        if payouts:
            put("payout_3y", sum(payouts) / len(payouts), "%", f"近 {len(payouts)} 年平均分红率",
                "dividend", f"{end_y - 2}-{end_y}", "shareholder")

    # ---------- 估值（本地因子快照）
    pe = valuation.get("pe")
    put("pe", pe, "倍", "市盈率", "factor_history", valuation.get("as_of"), "valuation")
    put("pb", valuation.get("pb"), "倍", "市净率", "factor_history", valuation.get("as_of"), "valuation")
    put("dividend_yield", valuation.get("dividend_yield"), "%", "股息率", "factor_history",
        valuation.get("as_of"), "shareholder")
    put("market_cap", valuation.get("market_cap"), "亿元", "总市值", "factor_history", valuation.get("as_of"), "valuation")
    pe_hist = [v for v in valuation.get("pe_hist") or [] if v and v > 0]
    pb_hist = [v for v in valuation.get("pb_hist") or [] if v and v > 0]
    if pe and pe > 0 and len(pe_hist) >= 120:
        put("pe_pct_self", _pct_rank(pe_hist, pe), "%", f"市盈率自身历史分位（{len(pe_hist)} 个交易日）",
            "factor_history", valuation.get("as_of"), "valuation")
    pb = valuation.get("pb")
    if pb and pb > 0 and len(pb_hist) >= 120:
        put("pb_pct_self", _pct_rank(pb_hist, pb), "%", f"市净率自身历史分位（{len(pb_hist)} 个交易日）",
            "factor_history", valuation.get("as_of"), "valuation")
    peers = [v for v in valuation.get("industry_pes") or [] if v and v > 0]
    if pe and pe > 0 and len(peers) >= 10:
        put("pe_pct_industry", _pct_rank(peers, pe), "%", f"市盈率同行业分位（{len(peers)} 家）",
            "factor_history", valuation.get("as_of"), "valuation")

    # ---------- 同业对比（本地因子快照的同行业最新截面；只展示不计分）
    peers = None
    rows = valuation.get("industry_rows") or []
    self_code = valuation.get("self_code")
    me = next((r for r in rows if r["code"] == self_code), None)
    if me is not None and len(rows) >= 2:
        as_of = valuation.get("as_of")
        n = len(rows)

        def vals(key: str, positive: bool = False) -> list[float]:
            return [v for r in rows if (v := r.get(key)) is not None and (v > 0 or not positive)]

        put("peers_count", float(n), "", "同行业样本数", "factor_history", as_of, "peers")
        put("roe_snapshot", me.get("roe"), "%", "ROE（行情快照口径）", "factor_history", as_of, "peers")
        for key, label, positive in (("roe", "ROE", False), ("pe", "市盈率", True), ("pb", "市净率", True),
                                     ("dividend_yield", "股息率", False)):
            v = vals(key, positive)
            if v:
                unit = "%" if key in ("roe", "dividend_yield") else "倍"
                put(f"industry_median_{key}", statistics.median(v), unit, f"同行业{label}中位数",
                    "factor_history", as_of, "peers")
        if n >= 10:
            if me.get("roe") is not None:
                put("roe_pct_industry", _pct_rank(vals("roe"), me["roe"]), "%",
                    f"ROE 同行业分位（{n} 家，行情快照口径）", "factor_history", as_of, "peers")
            pb_vals = vals("pb", True)
            if me.get("pb") and me["pb"] > 0:
                put("pb_pct_industry", _pct_rank(pb_vals, me["pb"]), "%", f"市净率同行业分位（{len(pb_vals)} 家）",
                    "factor_history", as_of, "peers")
        # 对比表：市值（对数距离）最接近的 6 家——用行业龙头对比区域小盘股没有意义
        others = [r for r in rows if r["code"] != self_code and (r.get("market_cap") or 0) > 0]
        my_cap = me.get("market_cap") or 0
        if my_cap > 0:
            others.sort(key=lambda r: abs(math.log(r["market_cap"] / my_cap)))
        table = [{**r, "self": False} for r in others[:6]] + [{**me, "self": True}]
        table.sort(key=lambda r: r.get("market_cap") or 0, reverse=True)
        peers = {"industry": valuation.get("industry"), "count": n, "as_of": as_of, "rows": table}

    # ---------- 公司概况与主营构成
    profile_rec = (raw.get("profile") or [{}])[0] if raw.get("profile") else {}
    profile = {
        k: str(profile_rec.get(k) or "")[:limit]
        for k, limit in (
            ("公司名称", 80), ("所属行业", 40), ("主营业务", 300), ("上市日期", 20),
            ("机构简介", 500), ("入选指数", 200), ("所属市场", 20),
        )
    }

    segments: list[dict] = []
    zygc = raw.get("zygc") or []
    if zygc:
        latest = max(norm_date(r.get("报告日期")) or "" for r in zygc)
        rows = [r for r in zygc if norm_date(r.get("报告日期")) == latest]
        for kind in ("按产品分类", "按行业分类", "按地区分类"):
            picked = [r for r in rows if r.get("分类类型") == kind and "其他" not in str(r.get("主营构成"))]
            if picked:
                break
        picked.sort(key=lambda r: num(r.get("收入比例")) or 0, reverse=True)
        for i, r in enumerate(picked[:5], 1):
            seg = str(r.get("主营构成"))
            share, gm = num(r.get("收入比例")), num(r.get("毛利率"))
            put(f"seg_{i}_share", None if share is None else share * 100, "%", f"{seg} 收入占比", "zygc", latest, "segment")
            put(f"seg_{i}_gm", None if gm is None else gm * 100, "%", f"{seg} 毛利率", "zygc", latest, "segment")
            segments.append({"name": seg, "share_fact": f"seg_{i}_share", "gm_fact": f"seg_{i}_gm"})

    # ---------- 公告事件
    events = None
    if raw.get("notices") is not None:
        events = []
        for r in raw["notices"]:
            title = str(r.get("公告标题") or "").strip()
            if not title:
                continue
            tag = classify_notice(title)
            events.append({
                "date": norm_date(r.get("公告时间")), "title": title,
                "url": r.get("公告链接"), "tag": tag, "label": EVENT_LABELS.get(tag, "其他"),
            })
        events.sort(key=lambda e: e["date"] or "", reverse=True)

    # ---------- 新闻
    news = [
        {"title": str(r.get("新闻标题") or ""), "time": str(r.get("发布时间") or ""),
         "source": str(r.get("文章来源") or ""), "url": r.get("新闻链接")}
        for r in (raw.get("news") or [])[:10]
    ]

    # ---------- 机构研报一致预期（近 90 天）
    consensus = None
    reports = raw.get("reports")
    if reports is not None:
        cutoff = date.today().toordinal() - 90
        recent = [
            r for r in reports
            if (d := norm_date(r.get("日期"))) and date.fromisoformat(d).toordinal() >= cutoff
        ]
        ratings: dict[str, int] = {}
        for r in recent:
            k = str(r.get("东财评级") or "").strip() or "未评级"
            ratings[k] = ratings.get(k, 0) + 1
        put("inst_reports_90d", float(len(recent)), "篇", "近 90 天机构研报数", "reports", None, "consensus")
        for k, n in ratings.items():
            put(f"inst_rating_{k}", float(n), "篇", f"近 90 天评级「{k}」研报数", "reports", None, "consensus")
        fc_years = sorted({int(m.group(1)) for k in (reports[0] if reports else {}) if (m := re.match(r"(\d{4})-盈利预测-收益", k))})
        price = valuation.get("price")
        eps_mean: dict[int, float] = {}
        for y in fc_years[:2]:
            eps_vals = [v for r in recent if (v := num(r.get(f"{y}-盈利预测-收益"))) is not None]
            pe_vals = [v for r in recent if (v := num(r.get(f"{y}-盈利预测-市盈率"))) is not None]
            if eps_vals:
                mean = sum(eps_vals) / len(eps_vals)
                eps_mean[y] = mean
                put(f"inst_eps_fc_{y}", mean, "元", f"机构预测 {y} 年每股收益（均值）",
                    "reports", str(y), "consensus")
                put(f"inst_eps_fc_n_{y}", float(len(eps_vals)), "篇", f"给出 {y} 年盈利预测的研报数",
                    "reports", str(y), "valuation")
                # 分歧：样本太少时区间 / 离散度没有意义
                if len(eps_vals) >= 3:
                    put(f"inst_eps_fc_min_{y}", min(eps_vals), "元", f"机构预测 {y} 年每股收益（最低）",
                        "reports", str(y), "valuation")
                    put(f"inst_eps_fc_max_{y}", max(eps_vals), "元", f"机构预测 {y} 年每股收益（最高）",
                        "reports", str(y), "valuation")
                    if mean > 0:
                        put(f"inst_eps_fc_cv_{y}", statistics.pstdev(eps_vals) / mean * 100, "%",
                            f"机构预测 {y} 年每股收益离散度（标准差 / 均值）", "reports", str(y), "valuation")
                # 前瞻市盈率用当前股价重算；研报里的市盈率是各自发布日的股价
                if price and mean > 0:
                    put(f"fwd_pe_{y}", price / mean, "倍", f"前瞻市盈率（当前股价 / 机构预测 {y} 年每股收益）",
                        "reports", str(y), "valuation")
                # 一致预期隐含增速：对上一年实际值，没有实际值就对上一年预测值
                base = num((by_year.get(y - 1) or {}).get("基本每股收益")) or eps_mean.get(y - 1)
                if base and base > 0:
                    put(f"inst_eps_growth_fc_{y}", (mean / base - 1) * 100, "%",
                        f"机构预测 {y} 年每股收益增速（对 {y - 1} 年{'实际' if y - 1 in by_year else '预测'}）",
                        "reports", str(y), "valuation")
            if pe_vals:
                put(f"inst_pe_fc_{y}", sum(pe_vals) / len(pe_vals), "倍", f"机构预测 {y} 年市盈率（均值）",
                    "reports", str(y), "consensus")

        # 预测完成度：最新累计每股收益占当年预测的比例，对照去年同期占全年的比例（季节性基准）
        if periodic:
            lp_d = norm_date(periodic[-1].get("报告期"))
            ly = int(lp_d[:4])
            cum = num(periodic[-1].get("基本每股收益"))
            prev = next((r for r in periodic if norm_date(r.get("报告期")) == _year_ago(lp_d)), None)
            if not lp_d.endswith("12-31") and ly in eps_mean and cum is not None and eps_mean[ly] > 0:
                put("inst_eps_progress", cum / eps_mean[ly] * 100, "%",
                    f"截至 {lp_d} 每股收益占 {ly} 年机构预测的比例", "reports", lp_d, "valuation")
                prev_cum = num(prev.get("基本每股收益")) if prev else None
                prev_full = num((by_year.get(ly - 1) or {}).get("基本每股收益"))
                if prev_cum is not None and prev_full and prev_full > 0:
                    put("eps_progress_prev", prev_cum / prev_full * 100, "%",
                        f"{ly - 1} 年同期每股收益占全年实际的比例", "abstract_report", _year_ago(lp_d), "valuation")
        consensus = {
            "ratings": ratings,
            "titles": [
                {"date": norm_date(r.get("日期")), "org": r.get("机构"), "title": r.get("报告名称"),
                 "rating": r.get("东财评级"), "url": r.get("报告PDF链接")}
                for r in recent[:8]
            ],
        }

    # 连续亏损排雷要用到最近两个完整年度的扣非
    deducted_last2 = [num(by_year[y].get("扣非净利润")) for y in sorted(by_year)[-2:]]

    return {
        "facts": facts,
        "data_as_of": data_as_of,
        "profile": profile,
        "segments": segments,
        "events": events,
        "news": news,
        "consensus": consensus,
        "is_st": "ST" in (name or "").upper(),
        "deducted_last2": deducted_last2,
        "peers": peers,
    }
