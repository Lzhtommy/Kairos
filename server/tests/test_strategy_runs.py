"""盘后策略运行：diff 正确性、幂等、通知触达。"""

from datetime import datetime

import pytest

from app.jobs import strategy_runs
from app.models.strategy import Strategy, StrategyRun
from app.models.user import User, UserSetting
from tests.conftest import add_stock, trading_days

DSL = {
    "universe": {"exclude": ["ST"], "market": ["SH", "SZ"]},
    "filters": [{"factor": "pe", "op": "lte", "value": 50}],
    "technical": [],
}


@pytest.fixture()
def env(db, monkeypatch):
    """接管 job 的 SessionLocal → 测试内存库；收集发出的通知。"""
    monkeypatch.setattr(strategy_runs, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)  # job 会 close，测试内复用
    sent: list[tuple] = []
    monkeypatch.setattr(
        strategy_runs.notify, "send", lambda *a: sent.append(a) or True
    )
    days = trading_days("2026-01-05", 10)
    add_stock(db, "600519", days, [10.0] * 10, pe=10)
    add_stock(db, "600000", days, [20.0] * 10, pe=30)
    user = User(email="t@t.co", password_hash="x", nickname="t")
    db.add(user)
    db.commit()
    db.add(UserSetting(user_id=user.id, notify_channel="webhook", notify_target="http://x"))
    s = Strategy(user_id=user.id, name="低PE", dsl=DSL)
    db.add(s)
    db.commit()
    return db, s, sent, days


def test_first_run_is_baseline_no_notify(env):
    db, s, sent, days = env
    n = strategy_runs.run_all_strategies(force=True)
    assert n == 1
    run = db.query(StrategyRun).one()
    assert run.hit_count == 2
    assert run.added_count == 0 and run.removed_count == 0  # 首跑是基线
    assert not sent
    assert db.get(Strategy, s.id).hit_count == 2


def test_idempotent_same_day(env):
    db, s, sent, days = env
    assert strategy_runs.run_all_strategies(force=True) == 1
    assert strategy_runs.run_all_strategies(force=True) == 0  # 同日重跑不重复
    assert db.query(StrategyRun).count() == 1


def test_diff_and_notify_on_change(env):
    db, s, sent, days = env
    strategy_runs.run_all_strategies(force=True)
    # 人为把首跑改成"昨天"，并让 600000 掉出条件 → 今日 diff 应有调出
    run = db.query(StrategyRun).one()
    run.run_date = datetime(2020, 1, 1)
    db.query(type(run)).filter_by(id=run.id).update({"run_date": datetime(2020, 1, 1)})
    from app.models.market import Quote

    db.query(Quote).filter_by(code="600000").update({"pe": 999.0})
    db.commit()

    assert strategy_runs.run_all_strategies(force=True) == 1
    latest = (
        db.query(StrategyRun).order_by(StrategyRun.run_date.desc()).first()
    )
    assert latest.hit_count == 1
    assert latest.removed_count == 1
    assert [x["code"] for x in latest.removed] == ["600000"]
    assert len(sent) == 1  # 有变化 → 一条通知
    title, text = sent[0][2], sent[0][3]
    assert "低PE" in text and "调出 1" in text
