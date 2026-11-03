"""最近 N 根日 K 的取数：参照股日历剪枝后结果与全量窗口一致。"""

from app.services.backtest import _bars_by_code
from app.services.market import _sparks, recent_cutoff
from app.services.technical import series_by_code
from tests.conftest import add_stock, trading_days


def test_cutoff_none_when_reference_history_too_short(db):
    days = trading_days("2024-01-01", 30)
    add_stock(db, "600519", days, [100.0] * 30)
    db.commit()
    assert recent_cutoff(db, 20) is None


def test_pruned_windows_return_latest_bars(db):
    # 参照股和普通股都有 400 根，取 20 / 100 根时剪枝生效
    days = trading_days("2023-01-02", 400)
    add_stock(db, "600519", days, [100.0 + i for i in range(400)])
    add_stock(db, "000001", days, [10.0 + i * 0.01 for i in range(400)])
    # 停牌股：最后一根落在 50 个交易日前，仍在 1.5N+20 的余量内
    add_stock(db, "600000", days[:350], [5.0 + i * 0.01 for i in range(350)])
    db.commit()

    cutoff = recent_cutoff(db, 100)
    assert cutoff == days[-170]

    bars = series_by_code(db, ["000001", "600000"], 100)
    assert bars["000001"].close == [10.0 + i * 0.01 for i in range(300, 400)]
    assert len(bars["600000"].close) == 100
    assert bars["600000"].close[-1] == 5.0 + 349 * 0.01

    series = _bars_by_code(db, ["600519"], 100)["600519"]
    assert [s[0] for s in series] == days[-100:]

    sparks = _sparks(db, None, 20)
    assert sparks["600519"] == [100.0 + i for i in range(380, 400)]
    # 停牌超过余量（20 根 → 50 个交易日）的股票不再出 sparkline，而不是画几个月前的走势
    assert "600000" not in sparks
