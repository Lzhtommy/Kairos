"""注册邀请码：无效码拒绝、有效码放行并核销、已用码拒绝。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import router
from app.core.db import get_db
from app.models.user import InviteCode


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _register(client, email="a@b.com", code="CODE1"):
    return client.post(
        "/auth/register",
        json={"email": email, "password": "secret123", "nickname": "tester", "invite_code": code},
    )


def test_register_rejects_unknown_code(client):
    resp = _register(client, code="nope")
    assert resp.status_code == 400
    assert "无效" in resp.json()["detail"]


def test_register_requires_code_field(client):
    resp = client.post(
        "/auth/register",
        json={"email": "a@b.com", "password": "secret123", "nickname": "tester"},
    )
    assert resp.status_code == 422


def test_register_consumes_code(client, db):
    db.add(InviteCode(code="CODE1"))
    db.commit()

    resp = _register(client)
    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == "a@b.com"

    invite = db.query(InviteCode).filter_by(code="CODE1").one()
    assert invite.used_by == resp.json()["user"]["id"]
    assert invite.used_at is not None

    # 同一个码不能再用
    resp2 = _register(client, email="c@d.com")
    assert resp2.status_code == 400
    assert "已被使用" in resp2.json()["detail"]
