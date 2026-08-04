"""模拟盘结算引擎：入场/退出/容量/一字板/停机补结算的回归。

数据全部手工铺设（含 600519 日历参照股），一次 settle_all 逐日补结算，
期望值用最朴素的算术独立推导。
"""

from datetime import timedelta

import pytest

from app.models.market import Kline
from app.models.strategy import (
    PaperAccount,
    PaperEquity,
    PaperPosition,
    PaperTrade,
    Strategy,
    StrategyRun,
)
from app.models.user import User
from app.services import paper
from tests.conftest import add_stock, trading_days


def _bar(db, code, d, o, h, lo, c):
    db.add(Kline(code=code, period="1d", ts=d, open=o, high=h, low=lo, close=c, volume=1000))


def _setup(db, days, params) -> PaperAccount:
    """用户 + 策略 + 账户 + 日历参照股；last_settled 拨到 days[0] 之前以触发补结算。"""
    add_stock(db, "600519", days, [10.0] * len(days))  # 日历参照
    user = User(email="p@p.co", password_hash="x", nickname="p")
    db.add(user)
    db.commit()
    s = Strategy(user_id=user.id, name="纸上谈兵", dsl={"filters": [], "technical": []})
    db.add(s)
    db.commit()
    acct = PaperAccount(
        strategy_id=s.id, user_id=user.id, enabled=True,
        params=params, equity=1.0, last_settled=days[0] - timedelta(days=1),
    )
    db.add(acct)
    db.commit()
    return acct


def _run(db, acct, d, codes):
    db.add(StrategyRun(
        strategy_id=acct.strategy_id, run_date=d, hit_codes=codes, hit_count=len(codes)
    ))
    db.commit()


def test_hold_cycle_and_equity_math(db):
    days = trading_days("2026-02-02", 5)
    acct = _setup(db, days, {"holdDays": 2, "exitRule": "hold", "maxConcurrent": 2, "costRate": 0})
    _run(db, acct, days[0], ["600000"])
    _bar(db, "600000", days[0], 10.0, 10.0, 10.0, 10.0)   # 信号日收盘
    _bar(db, "600000", days[1], 10.0, 10.6, 9.9, 10.5)    # 次日开盘 10 入场
    _bar(db, "600000", days[2], 10.5, 11.0, 10.4, 11.0)
    _bar(db, "600000", days[3], 11.0, 11.6, 11.0, 11.55)  # 持有 2 日到期收盘卖
    db.commit()

    assert paper.settle_all(db) == 5  # d0..d4 逐日补结算

    trades = db.query(PaperTrade).all()
    assert len(trades) == 1
    t = trades[0]
    assert t.entry_px == 10.0 and t.exit_px == 11.55 and t.reason == "hold"
    assert t.ret == pytest.approx(15.5)
    assert db.query(PaperPosition).count() == 0  # 已清仓

    # 净值独立推导：每日收益 = 持仓日收益 / maxConcurrent(2)
    eq = 1.0
    for r in (10.5 / 10 - 1, 11 / 10.5 - 1, 11.55 / 11 - 1):
        eq *= 1 + r / 2
    assert db.get(PaperAccount, acct.id).equity == pytest.approx(eq, abs=1e-4)
    assert db.query(PaperEquity).count() == 5
    # 幂等：重复结算不产生新行
    assert paper.settle_all(db) == 0 or db.query(PaperEquity).count() == 5


def test_stop_loss_intraday(db):
    days = trading_days("2026-02-02", 3)
    acct = _setup(db, days, {"holdDays": 10, "exitRule": "stop",
                             "stopGain": 50, "stopLoss": 8, "costRate": 0})
    _run(db, acct, days[0], ["600000"])
    _bar(db, "600000", days[0], 10.0, 10.0, 10.0, 10.0)
    _bar(db, "600000", days[1], 10.0, 10.3, 9.9, 10.2)   # 入场 10
    _bar(db, "600000", days[2], 9.8, 9.9, 9.0, 9.1)      # 盘中击穿 9.2 → 止损价成交
    db.commit()

    paper.settle_all(db)
    t = db.query(PaperTrade).one()
    assert t.reason == "stop_loss"
    assert t.exit_px == pytest.approx(9.2)
    assert t.ret == pytest.approx(-8.0)


def test_capacity_skips_by_code_order(db):
    days = trading_days("2026-02-02", 2)
    acct = _setup(db, days, {"holdDays": 5, "exitRule": "hold",
                             "maxConcurrent": 1, "costRate": 0})
    _run(db, acct, days[0], ["600036", "600000"])
    for code in ("600000", "600036"):
        _bar(db, code, days[0], 10.0, 10.0, 10.0, 10.0)
        _bar(db, code, days[1], 10.0, 10.2, 9.9, 10.1)
    db.commit()

    paper.settle_all(db)
    pos = db.query(PaperPosition).all()
    assert [x.code for x in pos] == ["600000"]  # 代码序取前 1
    assert db.get(PaperAccount, acct.id).stats.get("skippedByCapacity") == 1


def test_one_word_limit_defers_entry(db):
    days = trading_days("2026-02-02", 3)
    acct = _setup(db, days, {"holdDays": 5, "exitRule": "hold",
                             "maxConcurrent": 2, "costRate": 0})
    _run(db, acct, days[0], ["600000"])
    _bar(db, "600000", days[0], 10.0, 10.0, 10.0, 10.0)
    _bar(db, "600000", days[1], 11.0, 11.0, 11.0, 11.0)   # 一字涨停（10%）买不进
    _bar(db, "600000", days[2], 11.2, 11.4, 11.1, 11.3)   # 次日打开 → 11.2 入场
    db.commit()

    paper.settle_all(db)
    pos = db.query(PaperPosition).one()
    assert pos.status == "open"
    assert pos.entry_px == pytest.approx(11.2)
    assert pos.defer_days == 1
