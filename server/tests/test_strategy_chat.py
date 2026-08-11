"""策略对话历史：读写往返、全量替换语义、越权 404。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.strategies import router
from app.core.db import get_db
from app.core.security import get_current_user
from app.models.strategy import Strategy
from app.models.user import User


def _make_user(db, email: str) -> User:
    user = User(email=email, password_hash="x", nickname=email.split("@")[0])
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _make_strategy(db, user: User) -> Strategy:
    s = Strategy(user_id=user.id, name="测试策略")
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@pytest.fixture()
def owner(db):
    return _make_user(db, "owner@x.com")


@pytest.fixture()
def client(db, owner):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: owner
    return TestClient(app)


def test_history_roundtrip(client, db, owner):
    s = _make_strategy(db, owner)
    assert client.get(f"/strategies/{s.id}/chat").json() == []

    messages = [
        {"role": "user", "text": "低PE高ROE"},
        {"role": "assistant", "text": "好的，策略如下", "code": "pe < 20"},
    ]
    resp = client.put(f"/strategies/{s.id}/chat", json={"messages": messages})
    assert resp.status_code == 200 and resp.json()["count"] == 2

    got = client.get(f"/strategies/{s.id}/chat").json()
    assert [m["role"] for m in got] == ["user", "assistant"]
    assert got[0]["code"] is None
    assert got[1]["code"] == "pe < 20"


def test_put_replaces_all(client, db, owner):
    s = _make_strategy(db, owner)
    client.put(
        f"/strategies/{s.id}/chat",
        json={"messages": [{"role": "user", "text": "旧对话"}]},
    )
    client.put(
        f"/strategies/{s.id}/chat",
        json={"messages": [{"role": "user", "text": "新对话"}, {"role": "assistant", "text": "回复"}]},
    )
    got = client.get(f"/strategies/{s.id}/chat").json()
    assert len(got) == 2
    assert got[0]["text"] == "新对话"


def test_foreign_strategy_404(client, db):
    other = _make_user(db, "other@x.com")
    s = _make_strategy(db, other)
    assert client.get(f"/strategies/{s.id}/chat").status_code == 404
    assert (
        client.put(f"/strategies/{s.id}/chat", json={"messages": []}).status_code == 404
    )
