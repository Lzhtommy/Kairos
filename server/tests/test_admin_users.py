"""管理员用户管理 API：非管理员 403、列表带策略数、设/撤管理员、重置密码、删除级联，且不能对自己操作。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.api.admin_users import router
from app.core.db import get_db
from app.core.security import get_current_user, verify_password
from app.models.strategy import Strategy
from app.models.user import InviteCode, User


def _make_user(db, email: str, is_admin: bool = False) -> User:
    user = User(email=email, password_hash="x", nickname=email.split("@")[0], is_admin=is_admin)
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


@pytest.fixture()
def admin(db):
    return _make_user(db, "admin@x.com", is_admin=True)


@pytest.fixture()
def admin_client(db, admin):
    return _client(db, admin)


def test_non_admin_forbidden(db):
    pleb = _make_user(db, "pleb@x.com")
    client = _client(db, pleb)
    assert client.get("/admin/users").status_code == 403
    assert client.patch(f"/admin/users/{pleb.id}", json={"isAdmin": True}).status_code == 403
    assert client.delete(f"/admin/users/{pleb.id}").status_code == 403


def test_list_with_strategy_count(admin_client, db):
    bob = _make_user(db, "bob@x.com")
    db.add_all([Strategy(user_id=bob.id, name="a"), Strategy(user_id=bob.id, name="b")])
    db.commit()

    users = admin_client.get("/admin/users").json()
    assert [u["email"] for u in users] == ["admin@x.com", "bob@x.com"]
    assert users[0]["isAdmin"] is True
    assert users[1]["strategyCount"] == 2


def test_toggle_admin(admin_client, db):
    bob = _make_user(db, "bob@x.com")
    resp = admin_client.patch(f"/admin/users/{bob.id}", json={"isAdmin": True})
    assert resp.status_code == 200
    assert resp.json()["isAdmin"] is True
    db.refresh(bob)
    assert bob.is_admin

    admin_client.patch(f"/admin/users/{bob.id}", json={"isAdmin": False})
    db.refresh(bob)
    assert not bob.is_admin


def test_cannot_act_on_self(admin_client, admin):
    assert admin_client.patch(f"/admin/users/{admin.id}", json={"isAdmin": False}).status_code == 400
    assert admin_client.post(f"/admin/users/{admin.id}/reset-password").status_code == 400
    assert admin_client.delete(f"/admin/users/{admin.id}").status_code == 400


def test_not_found(admin_client):
    assert admin_client.patch("/admin/users/99999", json={"isAdmin": True}).status_code == 404
    assert admin_client.delete("/admin/users/99999").status_code == 404


def test_reset_password(admin_client, db):
    bob = _make_user(db, "bob@x.com")
    password = admin_client.post(f"/admin/users/{bob.id}/reset-password").json()["password"]
    db.refresh(bob)
    assert verify_password(password, bob.password_hash)


def test_delete_cascades(admin_client, db):
    # 生产库在连接时打开外键约束，内存库这里手动打开以验证级联
    db.execute(text("PRAGMA foreign_keys=ON"))
    bob = _make_user(db, "bob@x.com")
    db.add_all([Strategy(user_id=bob.id, name="a"), InviteCode(code="USED", used_by=bob.id)])
    db.commit()

    assert admin_client.delete(f"/admin/users/{bob.id}").status_code == 200
    db.expire_all()
    assert db.get(User, bob.id) is None
    assert db.scalars(select(Strategy)).all() == []
    assert db.scalar(select(InviteCode.used_by).where(InviteCode.code == "USED")) is None
