"""除权除息检测与历史重标定：比例判定、缩放正确性、幂等、阈值边界。"""

from datetime import datetime

import pytest
from sqlalchemy import select

from app.jobs.collect import _apply_adjustments
from app.models.market import AdjustmentEvent, Kline
from app.models.strategy import PaperAccount, PaperPosition, Strategy
from app.models.user import User
from app.providers.base import QuoteData
from tests.conftest import add_stock, trading_days


def _quote(code: str, prev_close: float, ts: datetime) -> QuoteData:
    return QuoteData(
        code=code, price=prev_close, prev_close=prev_close, open=prev_close,
        high=prev_close, low=prev_close, volume=100, turnover=1.0,
        turnover_rate=1.0, pe=10.0, pb=2.0, market_cap=100.0, roe=15.0,
        exchange_ts=ts,
    )


def test_dividend_rescales_history_and_positions(db):
    days = trading_days("2026-03-02", 3)
    add_stock(db, "600000", days, [10.0, 10.0, 10.0])
    user = User(email="a@a.co", password_hash="x", nickname="a")
    db.add(user)
    db.commit()
    s = Strategy(user_id=user.id, name="s", dsl={})
    db.add(s)
    db.commit()
    acct = PaperAccount(strategy_id=s.id, user_id=user.id, params={})
    db.add(acct)
    db.commit()
    db.add(PaperPosition(
        account_id=acct.id, code="600000", status="open",
        signal_date=days[0], entry_date=days[1], entry_px=10.0, last_price=10.0,
    ))
    db.commit()

    ex_date = trading_days("2026-03-05", 1)[0]
    # 分红除权：交易所昨收调整为 9.0（10% 下调）
    assert _apply_adjustments(db, [_quote("600000", 9.0, ex_date)], ex_date) == 1

    closes = [
        r[0] for r in db.execute(
            select(Kline.close).where(Kline.code == "600000").order_by(Kline.ts)
        )
    ]
    assert closes == pytest.approx([9.0, 9.0, 9.0])  # 全段 ×0.9
    pos = db.query(PaperPosition).one()
    assert pos.entry_px == pytest.approx(9.0)  # 持仓成本同口径，浮盈不变
    ev = db.query(AdjustmentEvent).one()
    assert ev.ratio == pytest.approx(0.9)

    # 幂等：同日重跑不重复缩放
    assert _apply_adjustments(db, [_quote("600000", 9.0, ex_date)], ex_date) == 0
    closes2 = [
        r[0] for r in db.execute(select(Kline.close).where(Kline.code == "600000"))
    ]
    assert closes2 == pytest.approx([9.0, 9.0, 9.0])


def test_normal_price_gap_not_treated_as_adjustment(db):
    days = trading_days("2026-03-02", 3)
    add_stock(db, "600000", days, [10.0, 10.0, 10.0])
    ex_date = trading_days("2026-03-05", 1)[0]
    # 昨收一致（微小取整差）→ 不是除权
    assert _apply_adjustments(db, [_quote("600000", 9.995, ex_date)], ex_date) == 0
    # 涨停后的价差是行情不是除权（r > 1 区间不处理）
    assert _apply_adjustments(db, [_quote("600000", 11.0, ex_date)], ex_date) == 0
    assert db.query(AdjustmentEvent).count() == 0
