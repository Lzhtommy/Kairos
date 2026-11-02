"""自选股：下单 CSV 解析、板块归类、开盘涨跌幅、导入/覆盖/删除与权限。东财人气接口全部 monkeypatch。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.watchlist import router
from app.core.db import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.services import hot_rank
from app.services.watchlist import OrderFileError, board_of, open_changes, parse_order_csv
from tests.conftest import add_stock, trading_days

HEADER = "﻿算法类型,账户名称,算法实例,证券代码,任务数量,交易方向,开始时间,结束时间,涨跌停设置,过期后执行,其他参数\n"


def _row(code: str, qty: str, side: str, day: str = "20261030") -> str:
    return (
        f"VWAP,acct,kf_vwap_plus,{code},{qty},{side},{day}T092500000,{day}T093600000,0,0,"
        "头寸类型=普通头寸\n"
    )


def test_parse_keeps_only_buys_and_merges_duplicates():
    csv = HEADER + _row("000002", "800", "买入") + _row("000619", "11900", "卖出") \
        + _row("300750", "100.0", "买入") + _row("000002", "200", "买入")
    parsed = parse_order_csv(csv, "kf_order_ATX_2026-10-30.csv")
    assert parsed.trade_date == "2026-10-30"
    assert parsed.buys == {"000002": 1000, "300750": 100}


def test_parse_pads_codes_and_falls_back_to_filename_date():
    csv = HEADER + "VWAP,acct,x,2,100,买入,,,0,0,\n"
    parsed = parse_order_csv(csv, "kf_order_ATX_2026-11-02.csv")
    assert parsed.trade_date == "2026-11-02"
    assert parsed.buys == {"000002": 100}


@pytest.mark.parametrize(
    "content,msg",
    [
        ("a,b,c\n1,2,3\n", "缺少列"),
        (HEADER + _row("000002", "100", "卖出"), "没有标记为买入"),
        (HEADER + "VWAP,acct,x,000002,100,买入,,,0,0,\n", "无法确定交易日"),
    ],
)
def test_parse_rejects_bad_files(content, msg):
    with pytest.raises(OrderFileError, match=msg):
        parse_order_csv(content, "orders.csv")


def test_board_of():
    assert board_of("600519") == "主板"
    assert board_of("002594") == "主板"
    assert board_of("300750") == "创业板"
    assert board_of("301001") == "创业板"
    assert board_of("688001") == "科创板"
    assert board_of("920001") == "北交所"


def test_open_change_uses_trade_date_bar_vs_previous_close(db):
    days = trading_days("2026-10-26", 4)  # 26, 27, 28, 29
    add_stock(db, "000002", days, closes=[10, 10, 10, 11], opens=[10, 10, 10.5, 10.2])
    assert open_changes(db, ["000002"], "2026-10-28") == {"000002": 5.0}
    assert open_changes(db, ["000002"], "2026-10-29") == {"000002": 2.0}
    # 交易日还没有 bar（未开盘 / 未来日期）→ 缺值
    assert open_changes(db, ["000002"], "2026-10-30") == {}


def test_open_change_prefers_exchange_prev_close_when_local_bars_have_gaps(db):
    from datetime import datetime

    from app.models.market import Quote

    # 本地缺了 10-28 / 10-29 两根 bar，上一根是 10-27
    days = [datetime(2026, 10, 27), datetime(2026, 10, 30)]
    add_stock(db, "000002", days, closes=[10, 11], opens=[10, 11])
    q = db.get(Quote, "000002")
    q.prev_close = 10.5  # 交易所给的真实前收（10-29 收盘）
    db.commit()
    assert open_changes(db, ["000002"], "2026-10-30") == {"000002": 4.76}
    # 历史交易日不是快照所在日，只能用上一根 bar
    assert open_changes(db, ["000002"], "2026-10-27") == {}


def _make_user(db, name: str, is_admin: bool) -> User:
    user = User(username=name, password_hash="x", is_admin=is_admin)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _client(db, user: User) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


HISTORY = {"2026-10-29": 300, "2026-10-30": 250, "2026-11-02": 1}


@pytest.fixture(autouse=True)
def fake_eastmoney(monkeypatch):
    """东财接口全部打桩：实时排名恒为 42，历史排名见 HISTORY，"今天"固定 11-02。"""
    calls = {"history": 0}

    def history(_client, _code):
        calls["history"] += 1
        return dict(HISTORY)

    monkeypatch.setattr(hot_rank, "_fetch_latest", lambda _client, _code: 42)
    monkeypatch.setattr(hot_rank, "_fetch_history", history)
    monkeypatch.setattr(hot_rank, "today_cst", lambda: "2026-11-02")
    monkeypatch.setattr(hot_rank, "_cache", {})
    monkeypatch.setattr(hot_rank, "_his_tried", {})
    return calls


@pytest.fixture()
def admin_client(db):
    return _client(db, _make_user(db, "admin", is_admin=True))


def test_ensure_daily_stores_finished_days_once(db, fake_eastmoney):
    from app.models.market import HotRankDaily

    assert hot_rank.ensure_daily(db, ["000002"], {"2026-10-30"}) == 2
    # 当天（11-02）的值还在变，不落库
    rows = db.query(HotRankDaily).order_by(HotRankDaily.date).all()
    assert [(r.date, r.rank) for r in rows] == [("2026-10-29", 300), ("2026-10-30", 250)]
    # 已有就不再请求
    assert hot_rank.ensure_daily(db, ["000002"], {"2026-10-29", "2026-10-30"}) == 0
    assert fake_eastmoney["history"] == 1
    assert hot_rank.daily_ranks(db, ["000002", "300750"], "2026-10-30") == {"000002": 250}


def test_today_import_uses_realtime_rank_and_yesterday_from_db(admin_client, db):
    from app.models.market import HotRankDaily

    db.add(HotRankDaily(code="000002", date="2026-11-01", rank=777))
    db.commit()
    imp = admin_client.post(
        "/watchlist/imports", json={"content": HEADER + _row("000002", "100", "买入", "20261102")}
    ).json()
    detail = admin_client.get(f"/watchlist/imports/{imp['id']}/items").json()
    assert detail["prevDate"] == "2026-11-01"
    item = detail["items"][0]
    assert (item["hotRank"], item["hotRankPrev"]) == (42, 777)


def test_non_admin_forbidden(db):
    client = _client(db, _make_user(db, "pleb", is_admin=False))
    assert client.get("/watchlist/imports").status_code == 403
    assert client.post("/watchlist/imports", json={"content": "x"}).status_code == 403


def test_import_list_items_and_reimport_replaces(admin_client, db):
    days = trading_days("2026-10-27", 4)  # 27, 28, 29, 30
    add_stock(db, "000002", days, closes=[10, 10, 10, 10], opens=[10, 10, 10, 10.3], name="万科A")

    csv = HEADER + _row("000002", "800", "买入") + _row("300750", "100", "买入")
    r = admin_client.post("/watchlist/imports", json={"filename": "a.csv", "content": csv})
    assert r.status_code == 200
    body = r.json()
    assert (body["tradeDate"], body["count"], body["replaced"]) == ("2026-10-30", 2, False)

    detail = admin_client.get(f"/watchlist/imports/{body['id']}/items").json()
    items = {it["code"]: it for it in detail["items"]}
    assert items["000002"]["name"] == "万科A"
    assert items["000002"]["openPct"] == 3.0
    # 10-30 已过去：当日用落库的日排名，昨日为 10-29
    assert detail["prevDate"] == "2026-10-29"
    assert (items["000002"]["hotRank"], items["000002"]["hotRankPrev"]) == (250, 300)
    assert items["000002"]["quantity"] == 800
    # 没有行情的股票也列出，行情字段留空
    assert items["300750"]["board"] == "创业板"
    assert items["300750"]["openPct"] is None

    again = admin_client.post(
        "/watchlist/imports", json={"content": HEADER + _row("600519", "100", "买入")}
    ).json()
    assert again["replaced"] is True
    imports = admin_client.get("/watchlist/imports").json()
    assert [(i["tradeDate"], i["count"]) for i in imports] == [("2026-10-30", 1)]

    assert admin_client.delete(f"/watchlist/imports/{again['id']}").status_code == 204
    assert admin_client.get("/watchlist/imports").json() == []


def test_bad_file_is_400(admin_client):
    r = admin_client.post("/watchlist/imports", json={"content": "a,b\n1,2\n"})
    assert r.status_code == 400
    assert "缺少列" in r.json()["detail"]


def test_cannot_read_other_users_import(admin_client, db):
    imp_id = admin_client.post(
        "/watchlist/imports", json={"content": HEADER + _row("000002", "100", "买入")}
    ).json()["id"]
    other = _client(db, _make_user(db, "admin2", is_admin=True))
    assert other.get(f"/watchlist/imports/{imp_id}/items").status_code == 404
    assert other.delete(f"/watchlist/imports/{imp_id}").status_code == 404
