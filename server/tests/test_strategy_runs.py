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


def _add_future_bars(db, code: str, days: list, entry_open: float, close: float) -> None:
    """run_date 之后的日 K：首日开盘 = 入场价，收盘恒为 close。"""
    from app.models.market import Kline

    for i, d in enumerate(days):
        o = entry_open if i == 0 else close
        db.add(
            Kline(code=code, period="1d", ts=d, open=o,
                  high=max(o, close), low=min(o, close), close=close, volume=1000)
        )
    db.commit()


def test_forward_returns_backfill(env):
    """信号前瞻收益：次日开盘入场、D+N 收盘结算；按成熟度增量补记。"""
    db, s, sent, days = env
    strategy_runs.run_all_strategies(force=True)
    run = db.query(StrategyRun).one()
    assert run.hit_count == 2 and (run.forward or {}) == {}

    future = trading_days("2026-01-19", 10)  # env 的 K 线止于 01-16（run_date）
    # 600519：入场 10.0 → 收盘恒 11.0（+10%）；600000：入场 20.0 → 19.0（-5%）
    _add_future_bars(db, "600519", future[:3], 10.0, 11.0)
    _add_future_bars(db, "600000", future[:3], 20.0, 19.0)

    # 只有 3 根后市 bar：d1 成熟，d5/d10 未成熟
    assert strategy_runs._backfill_forward(db, future[2]) == 1
    fwd = db.get(StrategyRun, run.id).forward
    assert fwd["d1"] == {"n": 2, "avg": 2.5, "win": 50.0}  # (+10% − 5%) / 2
    assert "d5" not in fwd and "d10" not in fwd

    _add_future_bars(db, "600519", future[3:], 11.0, 11.0)
    _add_future_bars(db, "600000", future[3:], 19.0, 19.0)
    assert strategy_runs._backfill_forward(db, future[-1]) == 1
    fwd = db.get(StrategyRun, run.id).forward
    assert fwd["d5"] == {"n": 2, "avg": 2.5, "win": 50.0}
    assert fwd["d10"] == {"n": 2, "avg": 2.5, "win": 50.0}
    # 已完结（d10 在档）→ 再跑不重复计算
    assert strategy_runs._backfill_forward(db, future[-1]) == 0
