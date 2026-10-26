"""基本面研报：解析 / 事件分类 / 评分规则 / 数字校验 / API 权限与队列 / 流水线端到端（无网络、无 LLM）。"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.research import admin_router, router
from app.core.config import settings
from app.core.db import get_db
from app.core.security import get_current_user
from app.models.market import FactorSnapshot, StockInfo
from app.models.research import ResearchReport
from app.models.user import User
from app.services import research as research_service
from app.services.research_facts import build_facts, classify_notice, num
from app.services.research_score import linear, score
from app.services.research_writer import allowed_numbers, find_bad_numbers, find_banned, find_meta


# ---------------------------------------------------------------- 合成数据


def _sina(rows: dict[str, dict]) -> list[dict]:
    return [{"报告日": d.replace("-", ""), "类型": "合并期末", **v} for d, v in rows.items()]


def make_raw(*, goodwill: float = 0.0, deducted: tuple = (60, 70, 80, 90), notices: list[str] | None = None) -> dict:
    years = [2022, 2023, 2024, 2025]
    annual = [
        {"报告期": str(y), "净资产收益率": f"{18 + i}%", "销售毛利率": "40%", "销售净利率": "15%",
         "营业总收入": f"{100 * 1.1 ** i:.2f}亿", "扣非净利润": f"{deducted[i]}亿", "基本每股收益": 1.0}
        for i, y in enumerate(years)
    ]
    report = [{"报告期": "2026-06-30", "扣非净利润同比增长率": "12%", "营业总收入同比增长率": "10%"}]
    yi = 1e8
    income = {}
    for y in (2024, 2025, 2026):
        base = 25 * (1.1 ** (y - 2024))
        for q, md in enumerate(("03-31", "06-30", "09-30", "12-31"), 1):
            if y == 2026 and q > 2:
                break
            income[f"{y}-{md}"] = {"营业收入": base * q * yi, "净利润": 20 * q * yi}
    balance = {
        "2026-06-30": {"资产总计": 1000 * yi, "负债合计": 400 * yi, "流动资产合计": 600 * yi, "流动负债合计": 300 * yi,
                       "货币资金": 200 * yi, "商誉": goodwill * yi, "归属于母公司股东权益合计": 600 * yi,
                       "短期借款": 50 * yi, "长期借款": 50 * yi, "应收账款": 30 * yi, "合同负债": 8 * yi},
        "2025-06-30": {"资产总计": 900 * yi, "负债合计": 380 * yi, "应收账款": 28 * yi, "合同负债": 10 * yi},
    }
    cashflow = {
        f"{y}-12-31": {"经营活动产生的现金流量净额": 90 * yi, "购建固定资产、无形资产和其他长期资产所支付的现金": 30 * yi}
        for y in (2023, 2024, 2025)
    }
    return {
        "profile": [{"公司名称": "测试股份", "所属行业": "酒、饮料和精制茶制造业", "主营业务": "卖酒"}],
        "abstract_annual": annual,
        "abstract_report": report,
        "income": _sina(income),
        "balance": _sina(balance),
        "cashflow": _sina(cashflow),
        "dividend": [{"报告时间": f"{y}年报", "派息比例": 4.0} for y in (2023, 2024, 2025)],
        "zygc": [],
        "notices": [{"公告标题": t, "公告时间": "2026-09-01", "公告链接": "http://x"} for t in (notices or [])],
        "news": [],
        "reports": [],
    }


VALUATION = {"pe": 20.0, "pb": 3.0, "dividend_yield": 2.0, "pe_hist": [10 + i * 0.1 for i in range(200)],
             "pb_hist": [2 + i * 0.01 for i in range(200)], "industry_pes": [5.0 * i for i in range(1, 13)]}


# ---------------------------------------------------------------- 解析 & 分类


def test_num_parsing():
    assert num("272.43亿") == pytest.approx(272.43)
    assert num("1.47%") == pytest.approx(1.47)
    assert num("5000万") == pytest.approx(0.5)
    assert num("--") is None
    assert num(float("nan")) is None
    assert num("abc") is None


@pytest.mark.parametrize(
    ("title", "tag"),
    [
        ("关于回购公司A股股份的进展公告", "buyback"),
        ("关于回购注销部分限制性股票的公告", "other"),
        ("关于控股股东减持股份计划的公告", "reduce"),
        ("关于终止减持计划的公告", "other"),
        ("关于收到上海证券交易所问询函的公告", "inquiry"),
        ("关于收到中国证监会立案告知书的公告", "investigation"),
        ("2026年半年度业绩预增公告", "forecast_up"),
        ("2026年前三季度业绩预亏公告", "forecast_down"),
        ("2026年A股员工持股计划（草案）", "incentive"),
        ("2026年半年度报告", "other"),
    ],
)
def test_classify_notice(title, tag):
    assert classify_notice(title) == tag


# ---------------------------------------------------------------- 事实表 & 评分


def test_build_facts_core_metrics():
    ds = build_facts(make_raw(), VALUATION, "测试股份")
    f = ds["facts"]
    assert ds["data_as_of"] == "2026-06-30"
    assert f["roe"]["value"] == pytest.approx(21)
    assert f["debt_ratio"]["value"] == pytest.approx(40)
    assert f["current_ratio"]["value"] == pytest.approx(2)
    assert f["interest_debt_to_cash"]["value"] == pytest.approx(0.5)
    assert f["ocf_to_np_3y"]["value"] == pytest.approx(90 / 80)
    assert f["fcf_to_np_3y"]["value"] == pytest.approx(60 / 80)
    assert f["deducted_np_cagr_3y"]["value"] == pytest.approx(((90 / 60) ** (1 / 3) - 1) * 100, rel=1e-3)
    # 单季营收同比：每年同比 10%，变化 0pp
    assert f["rev_q_yoy_latest"]["value"] == pytest.approx(10, abs=1e-6)
    assert f["rev_q_yoy_change"]["value"] == pytest.approx(0, abs=1e-6)
    # 2025 年报分红已实施 → 取 2023-2025，每股派 0.4 / EPS 1.0
    assert f["payout_3y"]["value"] == pytest.approx(40)
    assert f["pe_pct_industry"]["value"] == pytest.approx(3 / 12 * 100)


def test_linear_both_directions():
    assert linear(20, 8, 5, 20) == 8
    assert linear(5, 8, 5, 20) == 0
    assert linear(12.5, 8, 5, 20) == pytest.approx(4)
    assert linear(50, 6, 70, 30) == pytest.approx(3)  # 反向：负债率越低越好
    assert linear(100, 6, 70, 30) == 0


def test_score_is_deterministic_and_graded():
    ds = build_facts(make_raw(), VALUATION, "测试股份")
    a, b = score(ds), score(ds)
    assert a == b
    assert a["coverage"] >= 0.9
    assert a["grade"] in ("A", "B", "C", "D")
    assert a["sub_scores"]["events"]["score"] == 5  # 无事件 = 基准分


def test_missing_dimension_excluded_and_low_coverage_no_grade():
    raw = make_raw()
    for k in ("abstract_annual", "abstract_report", "income", "balance", "cashflow"):
        raw[k] = None
    res = score(build_facts(raw, {}, "测试股份"))
    assert res["sub_scores"]["profitability"]["insufficient"]
    assert res["sub_scores"]["profitability"]["score"] is None
    assert res["grade"] is None


def test_loss_makes_pe_items_zero_not_missing():
    res = score(build_facts(make_raw(), VALUATION | {"pe": -5.0}, "测试股份"))
    items = {i["key"]: i for i in res["sub_scores"]["valuation"]["items"]}
    assert items["pe_pct_self"]["points"] == 0
    assert items["pe_pct_industry"]["points"] == 0


def test_red_flags_cap_grade():
    raw = make_raw(goodwill=300, notices=["关于收到中国证监会立案告知书的公告"])
    res = score(build_facts(raw, VALUATION, "*ST测试"))
    keys = {f["key"] for f in res["red_flags"]}
    assert {"st", "goodwill", "investigation"} <= keys
    assert res["grade"] == "D"  # ST / 立案调查直接定 D
    assert res["sub_scores"]["events"]["score"] == 0  # 5 - 5


def test_soft_red_flag_caps_at_c():
    res = score(build_facts(make_raw(goodwill=300), VALUATION, "测试股份"))
    assert [f["key"] for f in res["red_flags"]] == ["goodwill"]
    assert res["total_score"] >= 65  # 不封顶本该是 A / B
    assert res["grade"] == "C"


def _items(res: dict, dim: str) -> dict:
    return {i["key"]: i for i in res["sub_scores"][dim]["items"]}


def test_turned_loss_cagr_scores_zero_not_missing():
    res = score(build_facts(make_raw(deducted=(60, 70, 80, -5)), VALUATION, "测试股份"))
    item = _items(res, "growth")["deducted_np_cagr_3y"]
    assert item["points"] == 0 and "≤ 0" in item["note"]


def test_turnaround_cagr_stays_missing():
    res = score(build_facts(make_raw(deducted=(-10, 20, 40, 60)), VALUATION, "测试股份"))
    assert _items(res, "growth")["deducted_np_cagr_3y"]["points"] is None


def _loss_cashflow(raw: dict, ocf: float, capex: float) -> dict:
    yi = 1e8
    for d in list(raw["cashflow"]):
        d["经营活动产生的现金流量净额"] = ocf * yi
        d["购建固定资产、无形资产和其他长期资产所支付的现金"] = capex * yi
    for row in raw["income"]:
        if row["报告日"].endswith("1231"):
            row["净利润"] = -10 * yi
    return raw


@pytest.mark.parametrize(("ocf", "capex", "ocf_pts", "fcf_pts"), [(30, 10, 3.5, 2), (30, 50, 3.5, 0), (-5, 10, 0, 0)])
def test_cumulative_loss_cashflow_by_sign(ocf, capex, ocf_pts, fcf_pts):
    raw = _loss_cashflow(make_raw(), ocf, capex)
    res = score(build_facts(raw, VALUATION, "测试股份"))
    items = _items(res, "cashflow")
    assert items["ocf_to_np_3y"]["points"] == ocf_pts
    assert items["fcf_to_np_3y"]["points"] == fcf_pts
    assert res["sub_scores"]["cashflow"]["score"] is not None  # 不再因缺失被整维剔除


def test_short_self_history_shifts_weight_to_industry():
    short = score(build_facts(make_raw(), VALUATION, "测试股份"))  # 200 个交易日
    items = _items(short, "valuation")
    assert (items["pe_pct_self"]["max"], items["pb_pct_self"]["max"], items["pe_pct_industry"]["max"]) == (2.5, 1.5, 11)
    assert "200 个交易日" in items["pe_pct_self"]["note"]

    long_val = VALUATION | {"pe_hist": [10 + i * 0.004 for i in range(500)], "pb_hist": [2 + i * 0.004 for i in range(500)]}
    items = _items(score(build_facts(make_raw(), long_val, "测试股份")), "valuation")
    assert (items["pe_pct_self"]["max"], items["pe_pct_industry"]["max"]) == (5, 7)
    assert items["pe_pct_self"]["note"] is None


def test_same_event_tag_counted_once():
    raw = make_raw(notices=["关于回购公司股份的进展公告"] * 5)
    res = score(build_facts(raw, VALUATION, "测试股份"))
    assert res["sub_scores"]["events"]["score"] == 7


def test_consecutive_losses_flag():
    res = score(build_facts(make_raw(deducted=(10, 5, -3, -4)), VALUATION, "测试股份"))
    assert any(f["key"] == "losses" for f in res["red_flags"])


# ---------------------------------------------------------------- 校验


def test_number_validation():
    facts = {"roe": {"value": 32.5312}, "rev": {"value": -5.14}, "pe_pct": {"value": 40.0, "label": "市盈率自身历史分位（509 个交易日）"}}
    allowed = allowed_numbers(facts, ["近期新闻：茅台涨1.98%"])
    assert find_bad_numbers("ROE 达 32.53%，较上年下降 5.14%，2025年表现稳健，3 个维度", allowed) == []
    assert find_bad_numbers("ROE 达 32.5%，2026-06-30 当日上涨 1.98%", allowed) == []
    assert find_bad_numbers("2022-2025 年复合增速 32.53%", allowed) == []
    assert find_bad_numbers("近 509 个交易日中处于 40.00% 分位", allowed) == []
    assert find_bad_numbers("毛利率提升 7.77 个百分点", allowed) == ["7.77"]


def test_banned_words():
    assert find_banned("公司现金流稳健，建议买入，目标价 2000 元") == ["建议买入", "目标价"]
    assert find_banned("股东增持彰显信心") == []


def test_find_meta_flags_talk_about_materials():
    # 金徽酒报告原句
    assert find_meta("事实中未提供公司对各年度 ROE 变化及费用结构的解释，故此处仅陈述数据呈现的现象。") == [
        "事实中", "未提供公司", "故此处仅", "仅陈述",
    ]
    assert find_meta("据年报，销售费用增长主要来自品牌推广投入。") == []


# ---------------------------------------------------------------- API


def _user(db, email, is_admin):
    u = User(username=email.split("@")[0], email=email, password_hash="x", is_admin=is_admin)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _client(db, user):
    app = FastAPI()
    app.include_router(router)
    app.include_router(admin_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


@pytest.fixture()
def stocks(db):
    db.add_all([
        StockInfo(code="600519", name="贵州茅台", market="SH", industry="白酒"),
        StockInfo(code="600036", name="招商银行", market="SH", industry="股份制银行"),
    ])
    db.commit()


def test_non_admin_cannot_generate(db, stocks):
    c = _client(db, _user(db, "u@x.com", False))
    assert c.post("/admin/research", json={"codes": ["600519"]}).status_code == 403
    assert c.get("/admin/research/tasks").status_code == 403
    assert c.get("/research").status_code == 200


def test_generate_validation_and_dedupe(db, stocks):
    c = _client(db, _user(db, "a@x.com", True))
    res = c.post("/admin/research", json={"codes": ["600519, 600036\n000000"]}).json()
    by = {r["code"]: r for r in res}
    assert by["600519"]["ok"]
    assert by["600036"]["reason"] == "金融类暂不支持"
    assert by["000000"]["reason"] == "代码不存在"
    again = c.post("/admin/research", json={"codes": ["600519"]}).json()
    assert again[0]["reason"] == "已在队列中"

    r = db.get(ResearchReport, by["600519"]["id"])
    r.status = "done"
    db.commit()
    assert c.post("/admin/research", json={"codes": ["600519"]}).json()[0]["reason"].startswith("今日已生成")
    assert c.post("/admin/research", json={"codes": ["600519"], "force": True}).json()[0]["ok"]


def test_users_only_see_done_reports(db, stocks):
    admin = _user(db, "a@x.com", True)
    pleb = _user(db, "u@x.com", False)
    failed = ResearchReport(code="600519", name="贵州茅台", status="failed")
    db.add(failed)
    db.commit()
    assert _client(db, pleb).get(f"/research/{failed.id}").status_code == 404
    assert _client(db, admin).get(f"/research/{failed.id}").status_code == 200
    assert _client(db, pleb).get("/research").json() == []

    ac = _client(db, admin)
    assert ac.post(f"/admin/research/{failed.id}/retry").json()["status"] == "queued"
    assert ac.delete(f"/admin/research/{failed.id}").status_code == 200


# ---------------------------------------------------------------- 流水线端到端（打桩采集，无 LLM）


def test_run_report_end_to_end(db, stocks, monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "")
    raw = make_raw(notices=["关于回购公司股份的进展公告"])
    sources = [{"key": k, "label": k, "fetched_at": None, "status": "ok"} for k in raw]
    monkeypatch.setattr(research_service, "fetch_all", lambda _db, _code, _p=None: (raw, sources))
    for i in range(130):
        db.add(FactorSnapshot(
            code="600519", ts=datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=i),
            pe=15 + i * 0.1, pb=5, roe=20, turnover_rate=1, turnover=1, market_cap=100, change_pct=0, price=10,
            dividend_yield=0.02, industry="白酒",
        ))
    last = datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=129)
    for code, cap in (("000858", 300), ("600809", 80), ("000568", 50)):
        db.add(FactorSnapshot(code=code, ts=last, pe=20, pb=4, roe=15, turnover_rate=1, turnover=1, market_cap=cap,
                              change_pct=0, price=10, dividend_yield=0.03, industry="白酒"))
    db.commit()

    r = ResearchReport(code="600519", status="running")
    db.add(r)
    db.commit()
    research_service.run_report(db, r)

    assert r.status == "done"
    assert r.grade is not None and r.total_score is not None
    assert [s["key"] for s in r.sections][0] == "summary"
    secs = {s["key"]: s for s in r.sections}
    assert secs["peers"]["peers"]["industry"] == "白酒"  # 来自本地因子快照的同行业截面
    assert len(secs["peers"]["peers"]["rows"]) == 4 and secs["peers"]["peers"]["rows"][0]["dividend_yield"] == 3
    assert secs["tracking"]["tracking"][-1]["key"] == "next"
    assert len(r.sections[0]["observations"]) == 3
    assert "基本面研究报告" in r.content_md and "不构成投资建议" in r.content_md

    user = _user(db, "u@x.com", False)
    c = _client(db, user)
    listed = c.get("/research").json()
    assert listed[0]["id"] == r.id and listed[0]["grade"] == r.grade
    detail = c.get(f"/research/{r.id}").json()
    assert detail["sub_scores"]["valuation"]["items"][0]["key"] == "pe_pct_self"
    assert r.facts["dividend_yield"]["value"] == pytest.approx(2)  # 库存小数 → 百分数
    assert c.get("/research/stock/600519/history").json()[0]["id"] == r.id
    assert "基本面研究报告" in c.get(f"/research/{r.id}/markdown").text


def test_run_report_rejects_financial_by_csrc_industry(db, monkeypatch):
    db.add(StockInfo(code="000001", name="平安银行", market="SZ", industry="—"))
    db.commit()
    raw = make_raw()
    raw["profile"] = [{"所属行业": "货币金融服务"}]
    monkeypatch.setattr(research_service, "fetch_all", lambda _db, _code, _p=None: (raw, []))
    r = ResearchReport(code="000001", status="running")
    db.add(r)
    db.commit()
    with pytest.raises(ValueError, match="金融类"):
        research_service.run_report(db, r)


# ---------------------------------------------------------------- 定期报告深读接入（打桩巨潮与 LLM）


def _fixture_pages(name: str) -> list[str]:
    import json
    from pathlib import Path

    return json.loads((Path(__file__).parent / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))


def _meta(kind: str, year: int, aid: str) -> dict:
    doc = {"annual": "年报", "semi": "半年报", "q1": "一季报", "q3": "三季报"}[kind]
    mmdd = {"annual": "12-31", "semi": "06-30", "q1": "03-31", "q3": "09-30"}[kind]
    return {"kind": kind, "doc": f"{year} {doc}", "period_end": f"{year}-{mmdd}", "title": f"贵州茅台{year}{doc}",
            "date": "2026-04-25", "year": year, "announcement_id": aid, "pdf_url": "http://x", "detail_url": "http://x"}


def test_run_report_with_periodic_insights(db, stocks, monkeypatch):
    from app.services import research_writer
    from app.services.research_annual import extract_mdna, extract_quarterly

    monkeypatch.setattr(settings, "deepseek_api_key", "test-key")
    monkeypatch.setattr(settings, "ai_provider", "deepseek")
    monkeypatch.setattr(research_service, "fetch_all", lambda _db, _code, _p=None: (make_raw(), []))

    # 4–8 月场景：2025 年报 + 2026 一季报；半年报 / 三季报都是旧的
    metas = {"annual": _meta("annual", 2025, "A25"), "semi": _meta("semi", 2025, "S25"),
             "q1": _meta("q1", 2026, "Q126"), "q3": _meta("q3", 2025, "Q325")}
    monkeypatch.setattr(research_service, "latest_report_meta", lambda _code, kind: metas[kind])
    sections = {
        "annual": extract_mdna(_fixture_pages("annual_600519_2025")),
        "q1": extract_quarterly(_fixture_pages("q1_600519_2026")),
    }
    fetched: list[str] = []

    def fake_fetch(_db, _code, kind, meta, _p=None):
        fetched.append(kind)
        return {"report": meta, "pages": [[no, t] for no, t in sections[kind]]}, {
            "key": kind, "label": meta["title"], "fetched_at": None, "status": "ok"}

    monkeypatch.setattr(research_service, "fetch_report_text", fake_fetch)

    extract_calls: list[str] = []

    def fake_chat(messages, max_tokens=1500):
        system, user = messages[0]["content"], messages[-1]["content"]
        if system == research_writer.EXTRACT_PROMPT:
            extract_calls.append(user)
            if "2026 一季报" in user:
                return {"drivers": [{"topic": "cashflow", "claim": "经营现金流增加",
                                     "quote": "主要是公司控股子公司贵州茅台集团财务有限公司不可随时支取的同业存款净增加额减少"}],
                        "kpis": [{"name": "国内经销商数量", "value": "2098", "unit": "个", "quote": "国内 2098 6 261"}],
                        "risks": []}, 50
            return {
                "drivers": [
                    {"topic": "revenue", "claim": "茅台酒基酒设计产能 46,395.00 吨",
                     "quote": "2025 年茅台酒基酒设计产能为 46,395.00 吨，同比新增基酒产能 1,800.00 吨"},
                    {"topic": "margin", "claim": "编造的解释", "quote": "毛利率因提价大幅上升到百分之九十"},
                ],
                "kpis": [{"name": "成品酒期末库存", "value": "24,770.35", "unit": "吨",
                          "quote": "成品酒 半成品酒（含基础酒）\n24,770.35 315,207.51"}],
                "risks": [{"title": "宏观经济风险", "quote": "一是宏观经济风险；二是安全风险"}],
            }, 100
        if "observations" in user:
            return {"observations": ["观察一", "观察二", "观察三"]}, 10
        return {"markdown": "解读正文。"}, 10

    monkeypatch.setattr(research_writer, "_chat_json", fake_chat)

    r = ResearchReport(code="600519", status="running")
    db.add(r)
    db.commit()
    research_service.run_report(db, r)

    assert r.status == "done"
    assert fetched == ["q1", "annual"]  # 只下载选中的两份
    assert len(extract_calls) == 2
    assert "【2026 一季报·主要会计数据、财务指标变动原因】" in extract_calls[0]
    assert "【2025 年报·行业经营性信息】（第 15 页起" in extract_calls[1]
    secs = {s["key"]: s for s in r.sections}
    # 编造的引用被丢弃；条目带出处，按报告期由新到旧
    assert [(c["doc"], c["page"]) for c in secs["growth"]["citations"]] == [("2025 年报", 15)]
    assert [(c["doc"], c["page"]) for c in secs["health"]["citations"]] == [("2026 一季报", 2)]
    assert "citations" not in secs["profitability"]
    assert [k["doc"] for k in secs["overview"]["kpis"]] == ["2026 一季报", "2025 年报"]
    # 季报不披露风险，风险取年报
    assert [(x["doc"], x["page"]) for x in secs["risks"]["company_risks"]] == [("2025 年报", 23)]
    assert [s["key"] for s in r.sources if s["key"] in ("q1", "annual")] == ["q1", "annual"]
    assert "公司自述风险" in r.content_md and "2026 一季报 第 2 页" in r.content_md
    # 后续跟踪：代码生成、不调 LLM，排在风险提示之后；公司自述风险进入跟踪清单
    keys = [s["key"] for s in r.sections]
    assert keys.index("tracking") == keys.index("risks") + 1
    assert secs["tracking"]["ai"] is False and secs["tracking"]["md"] == ""
    assert any(t["key"] == "risk_宏观经济风险" for t in secs["tracking"]["tracking"])
    assert secs["health"]["info_facts"] == ["contract_liab", "contract_liab_yoy"]
    assert "参考指标（不计分）" in r.content_md and "## " in r.content_md

    # 再生成一次：两份抽取都命中缓存
    r2 = ResearchReport(code="600519", status="running")
    db.add(r2)
    db.commit()
    research_service.run_report(db, r2)
    assert len(extract_calls) == 2
    assert [c["doc"] for c in {s["key"]: s for s in r2.sections}["health"]["citations"]] == ["2026 一季报"]


def test_periodic_skipped_without_llm(db, monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "")

    def boom(*_a, **_k):
        raise AssertionError("不该查询定期报告")

    monkeypatch.setattr(research_service, "latest_report_meta", boom)
    monkeypatch.setattr(research_service, "fetch_report_text", boom)
    insights, sources, tokens = research_service.periodic_insights(db, "600519", lambda _t: None)
    assert insights is None and tokens == 0 and sources[0]["status"] == "failed"



# ---------------------------------------------------------------- 合同负债 / 同业对比 / 后续跟踪


def _peer_rows() -> list[dict]:
    # 本股 600519 市值 100；按市值离得近的 6 家入表；行业 12 家
    caps = [5000, 3000, 1000, 400, 150, 120, 100, 90, 80, 60, 30, 10]
    codes = ["000858", "600809", "000568", "002304", "000596", "603369", "600519", "603198", "600559",
             "000799", "603589", "000995"]
    return [{"code": c, "name": f"股{c}", "market_cap": cap, "pe": 10.0 + i, "pb": 1.0 + i * 0.5,
             "roe": 30.0 - i * 2, "dividend_yield": 1.0 + i * 0.2} for i, (c, cap) in enumerate(zip(codes, caps))]


def test_build_facts_contract_liabilities_and_peers():
    ds = build_facts(make_raw(), {**VALUATION, "self_code": "600519", "industry": "白酒",
                                  "industry_rows": _peer_rows()}, "测试")
    f = ds["facts"]
    assert f["contract_liab"]["value"] == pytest.approx(8)
    assert f["contract_liab_yoy"]["value"] == pytest.approx(-20)
    assert f["contract_liab"]["group"] == "health"
    # 本股 roe = 30 - 6*2 = 18：样本 12 家里严格小于 18 的有 5 家
    assert f["roe_pct_industry"]["value"] == pytest.approx(5 / 12 * 100)
    assert f["industry_median_roe"]["value"] == pytest.approx(19)
    assert f["industry_median_pe"]["value"] == pytest.approx(15.5)
    peers = ds["peers"]
    assert peers["industry"] == "白酒" and peers["count"] == 12
    # 市值（对数距离）最接近的 6 家 + 本股，按市值降序：30 比 400 离 100 更近
    assert [r["code"] for r in peers["rows"]] == ["000596", "603369", "600519", "603198", "600559", "000799", "603589"]
    assert [r["self"] for r in peers["rows"]].count(True) == 1


def test_build_facts_peers_need_enough_samples():
    rows = _peer_rows()[:4]
    ds = build_facts(make_raw(), {**VALUATION, "self_code": "002304", "industry": "白酒", "industry_rows": rows}, "测试")
    assert "roe_pct_industry" not in ds["facts"]  # < 10 家不算分位
    assert ds["peers"]["count"] == 4 and len(ds["peers"]["rows"]) == 4
    assert build_facts(make_raw(), VALUATION, "测试")["peers"] is None


def test_build_tracking():
    from app.services.research_tracking import build_tracking

    ds = build_facts(make_raw(goodwill=500, notices=["关于收到上海证券交易所问询函的公告"]), VALUATION, "测试")
    ds["facts"]["deducted_np_yoy"]["value"] = -24.28
    ds["facts"]["ar_yoy"] = {"value": 79.3, "unit": "%", "label": "应收账款同比", "source": "balance",
                             "period": "2026-06-30", "group": "cashflow"}
    result = score(ds)
    periodic = {
        "drivers": [
            {"topic": "other", "claim": "净利润下降主要原因是补缴税款", "quote": "q", "page": 23, "doc": "2026 半年报"},
            {"topic": "balance", "claim": "应收账款变动主要原因是应收销货款增加", "quote": "q", "page": 18,
             "doc": "2026 半年报"},
        ],
        "kpis": [],
        "risks": [{"title": "市场竞争风险", "quote": "q", "page": 21, "doc": "2026 半年报"}],
    }
    items = build_tracking(ds, result, periodic)
    by_key = {i["key"]: i for i in items}
    kinds = [i["kind"] for i in items]
    # 分组顺序：排雷 → 指标 → 公告 → 公司自述风险 → 下一期数据
    assert kinds == sorted(kinds, key=["flag", "metric", "event", "risk", "next"].index)
    assert by_key["flag_goodwill"]["kind"] == "flag"
    # 指标异常附上公司解释（取最新报告里关键词命中的第一条）
    assert by_key["deducted_np_yoy"]["explain"] == {"claim": "净利润下降主要原因是补缴税款", "doc": "2026 半年报", "page": 23}
    assert by_key["ar_yoy"]["explain"]["page"] == 18
    assert by_key["contract_liab_yoy"]["value"] == "-20.00%"  # 合同负债同比 -20% 触发
    assert by_key["event_inquiry"]["kind"] == "event"
    assert by_key["risk_市场竞争风险"]["note"] == "公司自述（2026 半年报 第 21 页）"
    assert by_key["next"]["label"] == "2026 三季报" and "10-31" in by_key["next"]["note"]
    # 同一指标不重复（得分偏低 + 异常规则都命中时只留一条）
    assert len([i for i in items if i["key"] == "deducted_np_yoy"]) == 1
    assert build_tracking(ds, result, None)[-1]["kind"] == "next"


def _with_forecasts(eps_2026: list[float], eps_2027: list[float]) -> dict:
    raw = make_raw()
    raw["abstract_report"] = [
        {"报告期": "2025-06-30", "基本每股收益": 0.8},
        {"报告期": "2026-06-30", "扣非净利润同比增长率": "-24%", "营业总收入同比增长率": "5%", "基本每股收益": 0.42},
    ]
    d = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    raw["reports"] = [
        {"日期": d, "机构": f"券商{i}", "报告名称": "点评", "东财评级": "买入",
         "2026-盈利预测-收益": a, "2026-盈利预测-市盈率": 20.0, "2027-盈利预测-收益": b, "2027-盈利预测-市盈率": 18.0}
        for i, (a, b) in enumerate(zip(eps_2026, eps_2027))
    ]
    return raw


def test_build_facts_consensus_expectations():
    ds = build_facts(_with_forecasts([0.56, 0.61, 0.69], [0.7, 0.8, 0.9]), {**VALUATION, "price": 16.78}, "测试")
    f = ds["facts"]
    assert f["inst_eps_fc_2026"]["value"] == pytest.approx(0.62)
    assert f["inst_eps_fc_min_2026"]["value"] == 0.56 and f["inst_eps_fc_max_2026"]["value"] == 0.69
    assert f["inst_eps_fc_cv_2026"]["value"] == pytest.approx(statistics.pstdev([0.56, 0.61, 0.69]) / 0.62 * 100, abs=1e-3)
    # 前瞻市盈率用当前股价，不用研报里的市盈率
    assert f["fwd_pe_2026"]["value"] == pytest.approx(16.78 / 0.62, abs=1e-3)
    assert f["fwd_pe_2026"]["group"] == "valuation"
    # 2026 对 2025 年实际（1.0），2027 对 2026 年预测（0.62）
    assert f["inst_eps_growth_fc_2026"]["value"] == pytest.approx(-38)
    assert "实际" in f["inst_eps_growth_fc_2026"]["label"]
    assert f["inst_eps_growth_fc_2027"]["value"] == pytest.approx((0.8 / 0.62 - 1) * 100, abs=1e-2)
    assert "预测" in f["inst_eps_growth_fc_2027"]["label"]
    # 完成度：0.42 / 0.62；去年同期 0.8 / 1.0
    assert f["inst_eps_progress"]["value"] == pytest.approx(67.74, abs=0.01)
    assert f["eps_progress_prev"]["value"] == pytest.approx(80)


def test_consensus_dispersion_needs_three_reports_and_price():
    f = build_facts(_with_forecasts([0.5, 0.7], [0.8, 0.9]), VALUATION, "测试")["facts"]
    assert f["inst_eps_fc_n_2026"]["value"] == 2
    assert "inst_eps_fc_min_2026" not in f and "inst_eps_fc_cv_2026" not in f
    assert "fwd_pe_2026" not in f  # 没有股价


def test_tracking_flags_slow_forecast_progress():
    from app.services.research_tracking import build_tracking
    from app.services.research_writer import _info_facts

    ds = build_facts(_with_forecasts([0.56, 0.61, 0.69], [0.7, 0.8, 0.9]), {**VALUATION, "price": 16.78}, "测试")
    item = next(i for i in build_tracking(ds, score(ds), None) if i["key"] == "inst_eps_progress")
    assert item["value"] == "67.74%" and "80.00%" in item["note"]
    info = _info_facts("valuation", ds["facts"])
    assert info[:2] == ["fwd_pe_2026", "inst_eps_growth_fc_2026"] and info[6] == "fwd_pe_2027" and info[-2:] == ["inst_eps_progress", "eps_progress_prev"]


def test_chat_json_retries_once_on_invalid_json(monkeypatch):
    from app.services import research_writer

    replies = iter(['{"ok":<|endoftext|>true}', '{"ok": true}'])

    class Resp:
        def __init__(self, content):
            self._body = {"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 7}}

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    monkeypatch.setattr(research_writer.httpx, "post", lambda *a, **kw: Resp(next(replies)))
    assert research_writer._chat_json([{"role": "user", "content": "x"}]) == ({"ok": True}, 7)

    replies = iter(["{bad", "{still bad"])
    with pytest.raises(ValueError):
        research_writer._chat_json([{"role": "user", "content": "x"}])
