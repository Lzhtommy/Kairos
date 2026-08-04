"""日 K 深度回补：整段重写语义（删旧插新、修平复权台阶）。"""

from sqlalchemy import select

from app.models.market import Kline
from app.providers.base import Candle
from scripts.backfill_kline import backfill_code
from tests.conftest import add_stock, trading_days


class FakeProvider:
    name = "fake"

    def __init__(self, candles):
        self._candles = candles

    def get_kline_history(self, code, bars):
        return self._candles[-bars:]


def test_backfill_rewrites_full_history(db):
    old_days = trading_days("2026-01-05", 3)
    add_stock(db, "600000", old_days, [99.0, 99.0, 99.0])  # 旧口径（未复权污染）

    new_days = trading_days("2025-12-01", 40)
    candles = [
        Candle(ts=d, open=10.0, high=10.5, low=9.9, close=10.2, volume=100)
        for d in new_days
    ]
    wrote = backfill_code(db, FakeProvider(candles), "600000", bars=40)
    assert wrote == 40

    rows = db.execute(
        select(Kline).where(Kline.code == "600000", Kline.period == "1d")
        .order_by(Kline.ts)
    ).scalars().all()
    assert len(rows) == 40  # 旧 3 根被整段替换，不是叠加
    assert all(r.close == 10.2 for r in rows)  # 全部来自新口径


def test_backfill_empty_history_keeps_existing(db):
    days = trading_days("2026-01-05", 3)
    add_stock(db, "600000", days, [10.0, 10.0, 10.0])
    wrote = backfill_code(db, FakeProvider([]), "600000", bars=40)
    assert wrote == 0
    n = db.execute(select(Kline).where(Kline.code == "600000")).scalars().all()
    assert len(n) == 3  # 拉不到数据不动旧数据
