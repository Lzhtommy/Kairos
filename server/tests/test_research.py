"""基本面研报：解析 / 事件分类 / 评分规则 / 数字校验 / API 权限与队列 / 流水线端到端（无网络、无 LLM）。"""

from __future__ import annotations

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
from app.services.research_writer import find_banned, find_bad_numbers, allowed_numbers


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
                       "短期借款": 50 * yi, "长期借款": 50 * yi, "应收账款": 30 * yi},
        "2025-06-30": {"资产总计": 900 * yi, "负债合计": 380 * yi, "应收账款": 28 * yi},
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
    assert res["grade"] in ("C", "D", None)
    assert res["sub_scores"]["events"]["score"] == 0  # 5 - 5


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


# ---------------------------------------------------------------- API


def _user(db, email, is_admin):
    u = User(email=email, password_hash="x", nickname=email.split("@")[0], is_admin=is_admin)
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
    db.commit()

    r = ResearchReport(code="600519", status="running")
    db.add(r)
    db.commit()
    research_service.run_report(db, r)

    assert r.status == "done"
    assert r.grade is not None and r.total_score is not None
    assert [s["key"] for s in r.sections][0] == "summary"
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
