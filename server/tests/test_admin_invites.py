"""管理员邀请码 API：非管理员 403、管理员可生成/列表/删除、已用码不可删。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import router
from app.core.db import get_db
from app.core.security import get_current_user
from app.models.user import InviteCode, User


def _make_user(db, email: str, is_admin: bool) -> User:
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
def admin_client(db):
    return _client(db, _make_user(db, "admin@x.com", is_admin=True))


def test_non_admin_forbidden(db):
    client = _client(db, _make_user(db, "pleb@x.com", is_admin=False))
    assert client.get("/admin/invites").status_code == 403
    assert client.post("/admin/invites", json={"count": 1}).status_code == 403


def test_generate_and_list(admin_client):
    created = admin_client.post("/admin/invites", json={"count": 3}).json()
    assert len(created) == 3
    assert all(c["usedBy"] is None for c in created)

    listed = admin_client.get("/admin/invites").json()
    assert len(listed) == 3


def test_delete_unused_only(admin_client, db):
    unused = InviteCode(code="FREE")
    user = _make_user(db, "someone@x.com", is_admin=False)
    used = InviteCode(code="TAKEN", used_by=user.id)
    db.add_all([unused, used])
    db.commit()

    assert admin_client.delete(f"/admin/invites/{unused.id}").status_code == 200
    assert admin_client.delete(f"/admin/invites/{used.id}").status_code == 400
    assert admin_client.delete("/admin/invites/99999").status_code == 404

    listed = admin_client.get("/admin/invites").json()
    assert [c["code"] for c in listed] == ["TAKEN"]
    assert listed[0]["usedBy"] == "someone@x.com"
