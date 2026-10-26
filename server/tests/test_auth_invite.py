"""注册 / 登录：邀请码核销、用户名规则、只能用用户名登录。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import router
from app.core.db import get_db
from app.core.security import hash_password
from app.models.user import InviteCode, User


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _invite(db, code="CODE1"):
    db.add(InviteCode(code=code))
    db.commit()


def _register(client, username="alice", code="CODE1", password="secret123"):
    return client.post(
        "/auth/register",
        json={"username": username, "password": password, "invite_code": code},
    )


def _login(client, username, password="secret123"):
    return client.post("/auth/login", json={"username": username, "password": password})


def test_register_rejects_unknown_code(client):
    resp = _register(client, code="nope")
    assert resp.status_code == 400
    assert "无效" in resp.json()["detail"]


def test_register_requires_code_field(client):
    resp = client.post("/auth/register", json={"username": "alice", "password": "secret123"})
    assert resp.status_code == 422


def test_register_consumes_code(client, db):
    _invite(db)

    resp = _register(client)
    assert resp.status_code == 200
    user = resp.json()["user"]
    assert user["username"] == "alice"
    assert "nickname" not in user
    assert "email" not in user

    invite = db.query(InviteCode).filter_by(code="CODE1").one()
    assert invite.used_by == user["id"]
    assert invite.used_at is not None

    # 同一个码不能再用
    resp2 = _register(client, username="bob")
    assert resp2.status_code == 400
    assert "已被使用" in resp2.json()["detail"]


def test_code_stays_used_after_user_deleted(client, db):
    _invite(db)
    user_id = _register(client).json()["user"]["id"]

    # 模拟管理员删用户：外键 ON DELETE SET NULL 会清掉 used_by
    db.delete(db.get(User, user_id))
    db.commit()
    invite = db.query(InviteCode).filter_by(code="CODE1").one()
    invite.used_by = None
    db.commit()

    resp = _register(client, username="mallory")
    assert resp.status_code == 400
    assert "已被使用" in resp.json()["detail"]


@pytest.mark.parametrize("bad", ["ab", "a" * 21, "has space", "中文名字", "a-b", "x@y.com"])
def test_register_rejects_bad_username(client, db, bad):
    _invite(db)
    resp = _register(client, username=bad)
    assert resp.status_code == 400
    assert "用户名" in resp.json()["detail"]
    # 校验失败不能消耗邀请码
    assert db.query(InviteCode).filter_by(code="CODE1").one().used_at is None


def test_username_is_case_insensitive(client, db):
    _invite(db, "C1")
    _invite(db, "C2")
    resp = _register(client, username="Alice_01", code="C1")
    assert resp.json()["user"]["username"] == "alice_01"

    dup = _register(client, username="ALICE_01", code="C2")
    assert dup.status_code == 400
    assert "已被占用" in dup.json()["detail"]
    # 占用检查失败也不消耗邀请码
    assert db.query(InviteCode).filter_by(code="C2").one().used_at is None

    assert _login(client, "ALICE_01").status_code == 200


def test_login_by_username(client, db):
    _invite(db)
    _register(client)

    resp = _login(client, "alice")
    assert resp.status_code == 200
    assert resp.json()["token"]

    bad = _login(client, "alice", password="wrong-pass")
    assert bad.status_code == 401
    assert bad.json()["detail"] == "用户名或密码错误"
    assert _login(client, "nobody").status_code == 401


def test_legacy_user_cannot_login_with_email(client, db):
    db.add(
        User(
            username="old",
            email="Old@Example.com",
            password_hash=hash_password("secret123"),
        )
    )
    db.commit()

    assert _login(client, "old@example.com").status_code == 401
    resp = _login(client, "old")
    assert resp.status_code == 200
    assert "email" not in resp.json()["user"]

