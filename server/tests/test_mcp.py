"""MCP 数据服务：API Key 自助管理、Key 鉴权、MCP 协议握手与工具调用、限额与吊销。"""

import json
from contextlib import asynccontextmanager
from datetime import date, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Route

from app.api import api_keys as api_keys_api
from app.core.config import settings
from app.core.db import get_db
from app.core.security import get_current_user
from app.mcp_server import server as mcp_server
from app.models.api_key import ApiKey, ApiKeyUsage
from app.models.market import Kline, StockInfo
from app.models.user import User
from app.models.warehouse import DailyBasic, FinReport
from app.services import api_keys

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def mcp_http():
    """会话管理器只能 run 一次：整个模块共用一个带 lifespan 的客户端。"""

    @asynccontextmanager
    async def lifespan(_app):
        async with mcp_server.mcp.session_manager.run():
            yield

    app = Starlette(routes=[Route("/api/mcp", endpoint=mcp_server.app, methods=["GET", "POST", "DELETE"])],
                    lifespan=lifespan)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def wired(db, monkeypatch):
    monkeypatch.setattr(mcp_server, "SessionLocal", lambda: db)
    api_keys._windows.clear()
    return db


def _user(db, name="alice", admin=False):
    u = User(username=name, password_hash="x", is_admin=admin)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _rest(db, user):
    app = FastAPI()
    app.include_router(api_keys_api.router)
    app.include_router(api_keys_api.admin_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _rpc(client, key, method, params=None, rid=1):
    headers = {**HEADERS, **({"Authorization": f"Bearer {key}"} if key else {})}
    return client.post("/api/mcp", headers=headers,
                       json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})


def _call(client, key, name, args=None):
    r = _rpc(client, key, "tools/call", {"name": name, "arguments": args or {}})
    assert r.status_code == 200, r.text
    return r.json()["result"]


def _data(result):
    assert not result["isError"], result
    assert len(result["content"]) == 1  # 整个返回值是一段 JSON 文本
    return json.loads(result["content"][0]["text"])


def _set_key(db, key_id, **values):
    """工具调用结束会 close 共享的测试会话，之前拿到的 ApiKey 对象已脱离会话，改它不会落库。"""
    db.query(ApiKey).filter(ApiKey.id == key_id).update(values)
    db.commit()


class TestKeyManagement:
    def test_create_list_revoke(self, db):
        alice, bob = _user(db), _user(db, "bob")
        c = _rest(db, alice)
        created = c.post("/me/api-keys", json={"name": "Claude Code"}).json()
        assert created["key"].startswith("kai_") and created["prefix"] == created["key"][:10]
        assert created["dailyLimit"] == settings.mcp_daily_limit
        stored = db.get(ApiKey, created["id"])
        assert stored.key_hash == api_keys.hash_key(created["key"]) and created["key"] not in stored.key_hash
        listed = c.get("/me/api-keys").json()
        assert [k["id"] for k in listed["keys"]] == [created["id"]] and "key" not in listed["keys"][0]
        # 别人的 Key 吊销不了
        assert _rest(db, bob).delete(f"/me/api-keys/{created['id']}").status_code == 404
        assert c.delete(f"/me/api-keys/{created['id']}").json() == {"ok": True}
        assert api_keys.lookup(db, created["key"]) is None

    def test_usage_history(self, db, monkeypatch):
        from datetime import timedelta

        alice = _user(db)
        k1, _ = api_keys.create_key(db, alice.id, "a")
        k2, _ = api_keys.create_key(db, alice.id, "b")
        today = date(2026, 11, 26)
        monkeypatch.setattr(api_keys, "today_cst", lambda: today)
        db.add_all([ApiKeyUsage(key_id=k1.id, day=today, calls=5), ApiKeyUsage(key_id=k2.id, day=today, calls=2),
                    ApiKeyUsage(key_id=k1.id, day=today - timedelta(days=3), calls=7),
                    ApiKeyUsage(key_id=k1.id, day=today - timedelta(days=40), calls=99)])  # 超出 30 天
        db.commit()
        body = _rest(db, alice).get("/me/api-keys").json()
        assert len(body["usage"]) == 30 and body["usage"][-1] == {"day": "2026-11-26", "calls": 7}
        assert body["usage"][-4]["calls"] == 7 and sum(d["calls"] for d in body["usage"]) == 14
        by_name = {k["name"]: k for k in body["keys"]}
        assert (by_name["a"]["usedToday"], by_name["a"]["used30d"]) == (5, 12)

    def test_max_active_keys(self, db, monkeypatch):
        monkeypatch.setattr(settings, "mcp_max_keys", 2)
        c = _rest(db, _user(db))
        ids = [c.post("/me/api-keys", json={"name": f"k{i}"}).json()["id"] for i in range(2)]
        assert c.post("/me/api-keys", json={"name": "k3"}).status_code == 400
        c.delete(f"/me/api-keys/{ids[0]}")  # 吊销后腾出名额
        assert c.post("/me/api-keys", json={"name": "k3"}).status_code == 200

    def test_admin_adjusts_limit(self, db):
        key, _plain = api_keys.create_key(db, _user(db).id, "x")
        admin = _rest(db, _user(db, "root", admin=True))
        assert admin.patch(f"/admin/api-keys/{key.id}", json={"dailyLimit": 5000}).json()["dailyLimit"] == 5000
        assert admin.get("/admin/api-keys").json()[0]["username"] == "alice"


class TestMcpEndpoint:
    def _seed(self, db):
        db.add_all([
            StockInfo(code="600519", name="贵州茅台", market="SH", industry="白酒", list_date=date(2001, 8, 27)),
            *(Kline(code="600519", period="1d", ts=datetime(2026, 11, d), open=1, high=2, low=1, close=1.5 + d,
                    volume=100) for d in (24, 25, 26)),
            DailyBasic(code="600519", trade_date=date(2026, 11, 25), close=1474.84, suspended=False, is_st=False,
                       pe_ttm=22.4, total_mv=1.84e12),
            FinReport(code="600519", report_date=date(2026, 9, 30), notice_date=date(2026, 10, 30), roe=24.1),
        ])
        db.commit()

    def test_rejects_missing_or_bad_key(self, wired, mcp_http):
        r = _rpc(mcp_http, None, "tools/list")
        assert r.status_code == 401 and "Authorization" in r.json()["error"]
        assert _rpc(mcp_http, "kai_nope", "tools/list").status_code == 401

    def test_handshake_list_and_call(self, wired, mcp_http):
        db = wired
        self._seed(db)
        _key, plain = api_keys.create_key(db, _user(db).id, "agent")
        key_id = _key.id
        init = _rpc(mcp_http, plain, "initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"}})
        assert init.status_code == 200 and init.json()["result"]["serverInfo"]["name"] == "Kairos A 股数据"
        names = {t["name"] for t in _rpc(mcp_http, plain, "tools/list").json()["result"]["tools"]}
        assert {"get_kline", "get_daily_basic", "get_financial_statements", "get_news"} <= names

        data = _data(_call(mcp_http, plain, "get_kline", {"code": "sh600519", "limit": 2}))
        assert data["columns"][0] == "date" and [r[0] for r in data["rows"]] == ["2026-11-25", "2026-11-26"]
        db_ = _data(_call(mcp_http, plain, "get_market_daily_basic", {"industry": "白酒"}))
        assert db_["trade_date"] == "2026-11-25" and db_["rows"][0]["name"] == "贵州茅台"
        fin = _data(_call(mcp_http, plain, "get_financial_indicators", {"code": "600519"}))
        assert fin[0]["roe"] == 24.1 and fin[0]["report_date"] == "2026-09-30"
        # 入参问题以工具错误返回，不是协议错误
        bad = _call(mcp_http, plain, "get_kline", {"code": "abc"})
        assert bad["isError"] and "6 位" in bad["content"][0]["text"]
        assert db.get(ApiKeyUsage, (key_id, api_keys.today_cst())).calls == 4  # 每次工具调用计一次

    def test_daily_and_minute_limits(self, wired, mcp_http, monkeypatch):
        db = wired
        self._seed(db)
        key, plain = api_keys.create_key(db, _user(db).id, "agent")
        key_id = key.id
        _set_key(db, key_id, daily_limit=2)
        assert not _call(mcp_http, plain, "list_industries")["isError"]
        assert not _call(mcp_http, plain, "list_industries")["isError"]
        over = _call(mcp_http, plain, "list_industries")
        assert over["isError"] and "上限 2" in over["content"][0]["text"]
        assert db.get(ApiKeyUsage, (key_id, api_keys.today_cst())).calls == 2  # 超限那次不计

        _set_key(db, key_id, daily_limit=100)
        monkeypatch.setattr(settings, "mcp_per_minute", 3)
        api_keys._windows.clear()
        results = [_call(mcp_http, plain, "list_industries")["isError"] for _ in range(4)]
        assert results == [False, False, False, True]

    def test_revoked_key_stops_working(self, wired, mcp_http):
        db = wired
        key, plain = api_keys.create_key(db, _user(db).id, "agent")
        key_id = key.id
        assert _rpc(mcp_http, plain, "tools/list").status_code == 200
        _set_key(db, key_id, revoked_at=datetime(2026, 11, 26))
        assert _rpc(mcp_http, plain, "tools/list").status_code == 401


def test_every_tool_runs_on_empty_db(db):
    """每个工具对空库都能正常返回或给出可读的入参错误（防 SQL / 字段名写错）。"""
    from app.mcp_server import tools

    args = {"code": "600519", "codes": ["600519"], "query": "茅台", "index_code": "000300",
            "indicator": "cpi_yoy", "trade_date": "2026-11-25"}
    for fn in tools.TOOLS:
        import inspect

        params = inspect.signature(fn).parameters
        kwargs = {k: v for k, v in args.items() if k in params}
        try:
            fn(db, **kwargs)
        except tools.UserError:
            pass
