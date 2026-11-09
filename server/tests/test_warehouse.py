"""数据仓库：解析 + 批量写库幂等、查阅 API（外部接口全部 monkeypatch）。"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api import data as data_api
from app.core.db import get_db
from kairos_data.store import upsert_rows
from app.core.security import get_current_user
from kairos_data import sync as warehouse
from app.models.market import Kline, StockInfo
from app.models.warehouse import (Announcement, Dividend, FinReport, HolderCount, IndexBar, IndexConstituent,
                                  Lockup, MacroSeries, SecurityBar, SecurityInfo, TopHolder)
from kairos_data.sync import delisted_bars as bars


def test_quarter_ends():
    assert warehouse.quarter_ends(3, date(2026, 11, 6)) == [
        date(2026, 9, 30), date(2026, 6, 30), date(2026, 3, 31)]
    assert warehouse.quarter_ends(2, date(2026, 2, 1)) == [date(2025, 12, 31), date(2025, 9, 30)]


def _fin(code: str, profit: float, notice: str | None = "2026-03-21 00:00:00") -> dict:
    return {"SECURITY_CODE": code, "REPORTDATE": "2025-12-31 00:00:00", "NOTICE_DATE": notice,
            "UPDATE_DATE": "2026-03-22 00:00:00", "BASIC_EPS": 2.07, "TOTAL_OPERATE_INCOME": 1.3e11,
            "PARENT_NETPROFIT": profit, "WEIGHTAVG_ROE": 9.15, "XSMLL": None, "ASSIGNDSCRPT": "10派3.60元"}


def test_financials_upsert_idempotent(db, monkeypatch):
    calls = []

    def fake(name, filter="", **_kw):
        calls.append(filter)
        return [_fin("000001", 4.2e10), _fin("600519", 8.6e10, notice=None)]

    monkeypatch.setattr(warehouse.eastmoney_dc, "fetch_report", fake)
    assert warehouse.refresh_financials(db, [date(2025, 12, 31)]) == 2
    monkeypatch.setattr(warehouse.eastmoney_dc, "fetch_report",
                        lambda *a, **k: [_fin("000001", 4.3e10)])  # 更正版本覆盖
    warehouse.refresh_financials(db, [date(2025, 12, 31)])
    db.expire_all()
    assert db.execute(select(func.count()).select_from(FinReport)).scalar() == 2
    pa = db.get(FinReport, ("000001", date(2025, 12, 31)))
    assert pa.net_profit == 4.3e10 and pa.notice_date == date(2026, 3, 21) and pa.gross_margin is None
    # 没有公告日时退回更新日
    assert db.get(FinReport, ("600519", date(2025, 12, 31))).notice_date == date(2026, 3, 22)
    assert "REPORTDATE='2025-12-31'" in calls[0] and "058001001" in calls[0]


def test_dividend_rows():
    rows = warehouse.dividend_rows([{
        "SECURITY_CODE": "688676", "REPORT_DATE": "2024-12-31 00:00:00", "PRETAX_BONUS_RMB": 5.06,
        "BONUS_RATIO": None, "IT_RATIO": 4, "PLAN_NOTICE_DATE": "2024-10-22 00:00:00",
        "EQUITY_RECORD_DATE": "2025-05-22 00:00:00", "EX_DIVIDEND_DATE": "2025-05-23 00:00:00",
        "NOTICE_DATE": "2025-05-16 00:00:00", "ASSIGN_PROGRESS": "实施分配", "IMPL_PLAN_PROFILE": "10派5.06元",
    }])
    assert rows == [{
        "code": "688676", "report_date": date(2024, 12, 31), "plan_notice_date": date(2024, 10, 22),
        "notice_date": date(2025, 5, 16), "record_date": date(2025, 5, 22), "ex_date": date(2025, 5, 23),
        "cash_per10": 5.06, "bonus_per10": None, "transfer_per10": 4.0,
        "progress": "实施分配", "plan": "10派5.06元",
    }]


def test_macro_shares_report_and_skips_nulls(db, monkeypatch):
    fetched = []

    def fake(name, filter="", sort="", max_pages=None, **_kw):
        fetched.append((name, filter))
        return [{"REPORT_DATE": "2026-09-01 00:00:00", "TRADE_DATE": "2026-10-20 00:00:00",
                 "SOLAR_DATE": "2026-11-06 00:00:00", "NATIONAL_SAME": 0.9, "BASE_SAME": None,
                 "EMM00166466": 1.5868, "LPR1Y": 3, "IR_RATE": 1.398}]

    monkeypatch.setattr(warehouse.eastmoney_dc, "fetch_report", fake)
    warehouse.refresh_macro(db)
    # 国债 6 个期限、LPR 两个期限共用一次请求
    assert len(fetched) == len(set(fetched))
    vals = dict(db.execute(select(MacroSeries.indicator, MacroSeries.value)).all())
    assert vals["cpi_yoy"] == 0.9 and vals["cgb_10y"] == 1.5868 and vals["lpr_1y"] == 3.0
    assert "ppi_yoy" not in vals  # 空值不落库


def test_announcement_rows_pick_requested_code():
    items = [
        {"art_code": "AN1", "title": "联合公告", "notice_date": "2026-11-07 00:00:00",
         "codes": [{"stock_code": "600000"}, {"stock_code": "600519"}],
         "columns": [{"column_name": "董事会决议"}]},
        {"art_code": "AN2", "title": "无代码", "notice_date": "2026-11-07 00:00:00", "codes": []},
    ]
    rows = warehouse.announcement_rows(items, code="600519")
    assert rows == [{"art_code": "AN1", "code": "600519", "title": "联合公告",
                     "category": "董事会决议", "ann_date": date(2026, 11, 7)}]


def test_delisted_bars_skip_suspended_and_convert_volume():
    out = bars([
        {"date": "2009-12-28", "open": "5.2", "high": "5.3", "low": "5.1", "close": "5.29",
         "volume": "123456", "tradestatus": "1"},
        {"date": "2009-12-29", "open": "5.29", "high": "5.29", "low": "5.29", "close": "5.29",
         "volume": "", "tradestatus": "0"},
    ])
    assert out == [{"period": "1d", "ts": datetime(2009, 12, 28), "open": 5.2, "high": 5.3,
                    "low": 5.1, "close": 5.29, "volume": 1234}]


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(data_api.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: object()
    return TestClient(app)


def test_profile_and_search(client, db):
    db.add_all([
        StockInfo(code="600519", name="贵州茅台", market="SH", industry="白酒", list_date=date(2001, 8, 27)),
        StockInfo(code="600001", name="邯郸钢铁", market="SH", industry="—", delist_date=date(2009, 12, 29)),
        Kline(code="600001", period="1d", ts=datetime(2009, 12, 28), open=1, high=1, low=1, close=1, volume=1),
    ])
    db.commit()
    upsert_rows(db, IndexConstituent, [
        {"index_code": "000300", "snapshot_date": date(2009, 1, 1), "code": "600001"},
        {"index_code": "000300", "snapshot_date": date(2026, 6, 15), "code": "600519"},
    ])
    upsert_rows(db, Dividend, warehouse.dividend_rows([{
        "SECURITY_CODE": "600519", "REPORT_DATE": "2025-12-31 00:00:00", "PRETAX_BONUS_RMB": 300}]))
    db.commit()

    found = client.get("/data/search", params={"q": "600"}).json()
    assert [s["code"] for s in found] == ["600519", "600001"]  # 在市排前
    p = client.get("/data/stock/600001").json()
    assert p["info"]["delistDate"] == "2009-12-29" and p["quote"] is None
    assert p["kline"] == {"first": "2009-12-28", "last": "2009-12-28", "count": 1}
    assert p["indexes"] == [{"index": "000300", "name": "沪深 300", "current": False,
                             "first": "2009-01-01", "last": "2009-01-01"}]
    upsert_rows(db, Lockup, [
        {"code": "600519", "free_date": date(2099, 1, 1), "shares_type": "首发", "free_shares": 1e6},
        {"code": "600519", "free_date": date(2001, 8, 27), "shares_type": "首发", "free_shares": 2e6},
    ])
    upsert_rows(db, HolderCount, [{"code": "600519", "end_date": date(2026, 9, 30), "holder_num": 160000}])
    upsert_rows(db, TopHolder, [
        {"code": "600519", "end_date": d, "kind": "all", "rank": 1, "holder_name": n, "holder_type": "",
         "shares_type": "", "change": ""}
        for d, n in ((date(2026, 6, 30), "旧"), (date(2026, 9, 30), "茅台集团"))
    ])
    db.commit()
    mt = client.get("/data/stock/600519").json()
    assert mt["indexes"][0]["current"] is True and mt["dividends"][0]["cashPer10"] == 300
    assert [x["upcoming"] for x in mt["lockups"]] == [True, False]
    assert mt["holderCount"] == [{"date": "2026-09-30", "num": 160000, "avgShares": None}]
    assert mt["topHolders"]["all"]["endDate"] == "2026-09-30"
    assert [h["name"] for h in mt["topHolders"]["all"]["items"]] == ["茅台集团"]
    assert mt["topHolders"]["float"] == {"endDate": None, "noticeDate": None, "items": []}
    assert mt["statements"] == {"periods": [], "noticeDates": [], "items": []}
    assert mt["trading"] == {"margin": [], "billboard": [], "blocks": []}
    assert client.get("/data/stock/999999").status_code == 404


def test_announcements_cache_and_fallback(client, db, monkeypatch):
    item = {"art_code": "AN9", "title": "年报", "notice_date": "2026-03-21 00:00:00",
            "codes": [{"stock_code": "600519"}], "columns": []}
    monkeypatch.setattr(data_api.eastmoney_data, "fetch_announcements", lambda *a, **k: ([item], 1))
    r = client.get("/data/stock/600519/announcements").json()
    assert r["total"] == 1 and r["items"][0]["artCode"] == "AN9"

    db.get(Announcement, "AN9").summary = "已有摘要"
    db.commit()

    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(data_api.eastmoney_data, "fetch_announcements", boom)
    r = client.get("/data/stock/600519/announcements").json()
    assert r["items"][0]["summary"] == "已有摘要"  # 接口挂了用库里的
    # 已有摘要直接返回，不再调 AI
    assert client.post("/data/announcements/AN9/summary").json()["summary"] == "已有摘要"


def test_macro_endpoints(client, db):
    upsert_rows(db, MacroSeries, [
        {"indicator": "cpi_yoy", "period": date(2026, 8, 1), "value": 0.5},
        {"indicator": "cpi_yoy", "period": date(2026, 9, 1), "value": 0.9},
    ])
    db.commit()
    cat = {m["key"]: m for m in client.get("/data/macro").json()}
    assert (cat["cpi_yoy"]["value"], cat["cpi_yoy"]["prev"], cat["cpi_yoy"]["period"]) == (0.9, 0.5, "2026-09-01")
    assert cat["ppi_yoy"]["value"] is None
    s = client.get("/data/macro/cpi_yoy").json()
    assert s["points"] == [["2026-08-01", 0.5], ["2026-09-01", 0.9]]
    assert client.get("/data/macro/nope").status_code == 404


def test_index_endpoints(client, db):
    db.add(StockInfo(code="600519", name="贵州茅台", market="SH", industry="白酒"))
    db.commit()
    upsert_rows(db, IndexBar, [
        {"index_code": "000300", "trade_date": date(2026, 11, 5), "open": 1, "high": 1, "low": 1, "close": 4000},
        {"index_code": "000300", "trade_date": date(2026, 11, 6), "open": 1, "high": 1, "low": 1, "close": 4040},
    ])
    upsert_rows(db, IndexConstituent, [
        {"index_code": "000300", "snapshot_date": date(2026, 10, 30), "code": "000001", "weight": 0.5},
        {"index_code": "000300", "snapshot_date": date(2026, 10, 30), "code": "600519", "weight": 4.2},
    ])
    db.commit()
    cat = {i["code"]: i for i in client.get("/data/index").json()}
    assert cat["000300"]["changePct"] == 1.0 and cat["000852"]["close"] is None
    d = client.get("/data/index/000300", params={"years": 40}).json()
    assert [m["code"] for m in d["members"]] == ["600519", "000001"] and d["members"][0]["name"] == "贵州茅台"
    assert d["points"][-1] == ["2026-11-06", 4040]
    assert client.get("/data/index/999999").status_code == 404


def test_search_and_security_profile(client, db):
    db.add(StockInfo(code="601166", name="兴业银行", market="SH", industry="银行"))
    db.commit()
    upsert_rows(db, SecurityInfo, [
        {"code": "113052", "exchange": "SH", "sec_type": "cb", "name": "兴业转债", "list_date": date(2022, 1, 14),
         "stock_code": "601166", "convert_price": 25.51},
        {"code": "510300", "exchange": "SH", "sec_type": "etf", "name": "沪深300ETF", "list_date": date(2012, 5, 28)},
    ])
    upsert_rows(db, SecurityBar, [{"code": "113052", "trade_date": date.today(), "open": 1, "high": 1, "low": 1,
                                   "close": 113.2, "amount": 3e8}])
    db.commit()
    hits = client.get("/data/search", params={"q": "兴业"}).json()
    assert [(h["code"], h["type"]) for h in hits] == [("601166", "stock"), ("113052", "cb")]
    p = client.get("/data/security/113052").json()
    assert p["stockName"] == "兴业银行" and p["points"][-1][1] == 113.2 and p["lastAmount"] == 3e8
    assert client.get("/data/security/999999").status_code == 404
