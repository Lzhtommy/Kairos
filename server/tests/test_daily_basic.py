"""每日指标（daily_basic）：采集解析、增量起点，以及回测按当日 ST / 板块规则判涨跌停、
组合模式按当日真实因子选股。"""

from contextlib import contextmanager
from datetime import date

import pytest
from sqlalchemy import select

from app.models.market import Kline, StockInfo
from app.models.warehouse import DailyBasic
from app.services import backtest
from kairos_data import sync
from tests.conftest import add_stock, trading_days

GOLDEN = [{"type": "ma_cross", "fast": 2, "slow": 3, "direction": "golden"}]
BASE = [20, 18, 16, 14, 14, 18, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35]


@pytest.fixture(autouse=True)
def _no_benchmark(monkeypatch):
    monkeypatch.setattr(backtest, "_benchmark_closes", lambda code: {})


def _basic(code, day, **kw):
    return DailyBasic(code=code, trade_date=day.date() if hasattr(day, "date") else day,
                      close=kw.pop("close", 10.0), preclose=kw.pop("preclose", 10.0),
                      suspended=kw.pop("suspended", False), is_st=kw.pop("is_st", False), **kw)


class TestLimitRule:
    def test_board_and_date_rules(self):
        lp = backtest._limit_pct
        assert lp("600000", False, date(2026, 1, 5)) == 0.10
        assert lp("600000", True, date(2026, 7, 3)) == 0.05
        assert lp("600000", True, date(2026, 7, 6)) == 0.10  # 主板 ST 改 10%
        assert lp("300750", False, date(2020, 8, 21)) == 0.10  # 创业板注册制前
        assert lp("300750", True, date(2020, 8, 21)) == 0.05
        assert lp("300750", True, date(2020, 8, 24)) == 0.20
        assert lp("688981", True, date(2019, 7, 22)) == 0.20
        assert lp("920001", False, date(2026, 1, 5)) == 0.30
        assert lp("430047", True, date(2022, 1, 5)) == 0.30


class TestStHistory:
    def _five_pct_board(self, db, name):
        days = trading_days("2026-01-05", len(BASE))
        closes = list(BASE)
        opens, highs, lows = list(closes), list(closes), list(closes)
        # 信号日 idx=5 的次日做成 +5% 一字板：ST 时是涨停，非 ST 时只是普通平盘 bar
        px = round(closes[5] * 1.05, 2)
        closes[6] = opens[6] = highs[6] = lows[6] = px
        # 无基准时交易日历取 600519 的日 K
        add_stock(db, "600519", days, closes, opens=opens, highs=highs, lows=lows, name=name)
        return days

    def test_st_on_that_day_blocks_entry(self, db):
        days = self._five_pct_board(db, name="股票甲")  # 现在已摘帽
        db.add_all(_basic("600519", d, is_st=True) for d in days)
        db.commit()
        result = backtest.run(db, {"filters": [], "technical": GOLDEN,
                                   "universe": {"market": ["SH"], "exclude": []}},
                              {"holdDays": 5, "costRate": 0})
        assert result["trades"][0]["entryDate"] == days[7].strftime("%Y-%m-%d")

    def test_without_history_falls_back_to_current_name(self, db):
        days = self._five_pct_board(db, name="股票甲")
        result = backtest.run(db, {"filters": [], "technical": GOLDEN},
                              {"holdDays": 5, "costRate": 0})
        assert result["trades"][0]["entryDate"] == days[6].strftime("%Y-%m-%d")


class TestPortfolioPointInTime:
    def test_selects_on_that_days_values(self, db):
        days = trading_days("2026-01-05", 40)
        up = [10 * 1.01**i for i in range(40)]
        # 今天四只的 PE 都是 10；历史上只有 600519、600004 和已退市的 600002 满足 PE ≤ 20
        add_stock(db, "600519", days, up)
        add_stock(db, "600000", days, up)
        add_stock(db, "600003", days, up)
        add_stock(db, "600004", days, up, name="*ST甲")  # 现在戴帽，当时不是 ST
        db.add(StockInfo(code="600002", name="已退市", market="SH", industry="—",
                         delist_date=date(2026, 6, 30)))
        db.add_all(Kline(code="600002", period="1d", ts=d, open=c, high=c, low=c, close=c,
                         volume=10_000) for d, c in zip(days, up))
        for d in days:
            db.add_all([
                _basic("600519", d, pe_ttm=10.0),
                _basic("600000", d, pe_ttm=50.0),
                _basic("600003", d, pe_ttm=5.0, suspended=True),
                _basic("600004", d, pe_ttm=8.0),
                _basic("600002", d, pe_ttm=6.0, total_mv=3e9),
            ])
        db.commit()

        dsl = {"filters": [{"factor": "pe", "op": "lte", "value": 20}], "technical": []}
        result = backtest.run(db, dsl, {"rebalance": "monthly"})
        assert result["metrics"]["pitPeriods"] == 2
        assert [r["holdings"] for r in result["rebalanceRecords"]] == [3, 3]

    def test_historical_st_excluded_by_universe(self, db):
        days = trading_days("2026-01-05", 40)
        up = [10 * 1.01**i for i in range(40)]
        add_stock(db, "600519", days, up)
        add_stock(db, "600000", days, up)
        db.add_all(_basic("600519", d, pe_ttm=10.0, is_st=True) for d in days)
        db.add_all(_basic("600000", d, pe_ttm=10.0) for d in days)
        db.commit()
        dsl = {"filters": [{"factor": "pe", "op": "lte", "value": 20}], "technical": []}
        result = backtest.run(db, dsl, {"rebalance": "monthly"})
        assert [r["holdings"] for r in result["rebalanceRecords"]] == [1, 1]


class TestSync:
    def test_rows_use_shares_in_effect(self):
        raw = [
            {"date": "2020-01-02", "close": "10", "preclose": "9.5", "volume": "100", "amount": "1000",
             "turn": "1.5", "tradestatus": "1", "isST": "0", "peTTM": "20", "pbMRQ": "2",
             "psTTM": "3", "pcfNcfTTM": "-4"},
            {"date": "2020-06-01", "close": "12", "preclose": "12", "volume": "0", "amount": "",
             "turn": "", "tradestatus": "0", "isST": "1", "peTTM": "", "pbMRQ": "", "psTTM": "",
             "pcfNcfTTM": ""},
        ]
        shares = [(date(2019, 12, 1), 1000), (date(2020, 3, 1), 2000)]
        a, b = sync.daily_basic_rows("000004", raw, shares)
        assert a["total_mv"] == 10_000 and a["pcf_ttm"] == -4 and not a["suspended"] and not a["is_st"]
        assert b["total_mv"] == 24_000 and b["suspended"] and b["is_st"]
        assert b["amount"] is None and b["pe_ttm"] is None
        # 早于第一条股本记录：市值为空
        assert sync.daily_basic_rows("000004", raw[:1], [(date(2021, 1, 1), 1)])[0]["total_mv"] is None

    def test_refresh_starts_after_last_day(self, db, monkeypatch):
        db.add_all([
            StockInfo(code="600000", name="甲", market="SH", industry="—", list_date=date(1999, 1, 4)),
            StockInfo(code="600001", name="乙", market="SH", industry="—", list_date=date(1999, 1, 4)),
            StockInfo(code="600002", name="退", market="SH", industry="—", list_date=date(1999, 1, 4),
                      delist_date=date(2020, 1, 1)),
            StockInfo(code="920001", name="北", market="BJ", industry="—"),
            _basic("600000", date(2026, 11, 6)),
        ])
        db.commit()

        @contextmanager
        def fake_session():
            yield object()

        calls = []

        def fake_fetch(_bs, symbol, start, end):
            calls.append((symbol, start, end))
            return []

        monkeypatch.setattr(sync.baostock, "session", fake_session)
        monkeypatch.setattr(sync.baostock, "fetch_daily_basic", fake_fetch)
        monkeypatch.setattr(sync, "_today_cst", lambda: date(2026, 11, 10))
        sync.refresh_daily_basic(db)
        assert calls == [("sh.600000", "2026-11-07", "2026-11-10"),
                         ("sh.600001", "2026-10-31", "2026-11-10")]

    def test_gaps_need_history_back_to_listing(self, db):
        db.add_all([
            StockInfo(code="600000", name="甲", market="SH", industry="—", list_date=date(2010, 1, 4)),
            StockInfo(code="600001", name="乙", market="SH", industry="—", list_date=date(2010, 1, 4)),
            StockInfo(code="600002", name="老", market="SH", industry="—", list_date=date(1990, 12, 19)),
            StockInfo(code="920001", name="北", market="BJ", industry="—"),
            _basic("600000", date(2010, 1, 4)),
            _basic("600001", date(2026, 11, 6)),  # 只有日更写入的最近几天
        ])
        db.commit()
        assert [(c, s) for c, _m, s in sync.daily_basic_gaps(db)] == [
            ("600001", date(2010, 1, 4)), ("600002", date(1990, 12, 19))]


class TestPitFundamentals:
    def _reports(self, db):
        from app.models.warehouse import Dividend, FinReport

        for period, notice, roe in [
            (date(2024, 12, 31), date(2025, 3, 20), 20.0),
            (date(2025, 9, 30), date(2025, 10, 28), 15.0),
            (date(2025, 12, 31), date(2026, 3, 25), 22.0),
            (date(2026, 3, 31), date(2026, 4, 25), 6.0),
            (date(2026, 9, 30), date(2026, 10, 30), 18.0),
        ]:
            db.add(FinReport(code="600519", report_date=period, notice_date=notice, roe=roe))
        db.add(Dividend(code="600519", report_date=date(2025, 12, 31), ex_date=date(2026, 6, 20),
                        cash_per10=30.0, progress="实施分配", plan=""))
        db.add(Dividend(code="600519", report_date=date(2024, 12, 31), ex_date=date(2025, 6, 20),
                        cash_per10=99.0, progress="实施分配", plan=""))
        db.commit()

    def test_roe_uses_only_announced_reports(self, db):
        self._reports(db)
        pit = backtest._PitFundamentals(db, date(2026, 1, 1), date(2026, 11, 10))
        assert pit.roe("600519", date(2026, 3, 1)) == 15.0 * 12 / 9  # 年报未出，上年同期缺 → 年化
        assert pit.roe("600519", date(2026, 4, 1)) == 22.0  # 年报
        assert pit.roe("600519", date(2026, 5, 1)) == 6.0 * 12 / 3  # Q1 缺上年同期 → 年化
        assert pit.roe("600519", date(2026, 11, 1)) == 22.0 + 18.0 - 15.0  # TTM
        assert pit.roe("000001", date(2026, 11, 1)) is None and not pit.covers("000001")

    def test_dividend_yield_trailing_year(self, db):
        self._reports(db)
        pit = backtest._PitFundamentals(db, date(2026, 1, 1), date(2026, 11, 10))
        assert pit.dividend_yield("600519", date(2026, 6, 19), 100.0) == 0.099  # 只有 2025-06 那次
        assert pit.dividend_yield("600519", date(2026, 7, 1), 100.0) == 0.03
        assert pit.dividend_yield("000001", date(2026, 7, 1), 10.0) == 0.0


def test_fin_revisions_archive_changed_values():
    from datetime import datetime, timezone

    now = datetime(2026, 11, 10, tzinfo=timezone.utc)
    base = {"notice_date": date(2026, 4, 25), "eps": 1.0, "deduct_eps": 0.9, "revenue": 100.0,
            "net_profit": 10.0, "roe": 5.0, "bps": 20.0}
    have = {"600519": base, "000001": {**base, "roe": None}, "000002": base}
    rows = [
        {"code": "600519", "report_date": date(2026, 3, 31), **base, "net_profit": 9.0},  # 更正
        {"code": "000001", "report_date": date(2026, 3, 31), **base},  # 补上缺值，不算
        {"code": "000002", "report_date": date(2026, 3, 31), **base, "roe": 5.0000001},  # 浮点噪声
        {"code": "300750", "report_date": date(2026, 3, 31), **base},  # 新股票
    ]
    out = sync.fin_revisions(have, rows, now)
    assert [(r["code"], r["net_profit"], r["revised_at"]) for r in out] == [("600519", 10.0, now)]


def test_hot_rank_date_round_trips_as_string(db):
    from app.models.market import HotRankDaily
    from app.services import hot_rank

    db.add(HotRankDaily(code="600519", date="2026-10-30", rank=3))
    db.commit()
    assert hot_rank.ranks_on(db, "2026-10-30") == {"600519": 3}
    assert db.execute(select(HotRankDaily.date)).scalar() == "2026-10-30"


def test_insert_bars_upserts_on_natural_key(db):
    from app.jobs.collect import _insert_bars
    from kairos_data.sources.base import Candle
    from datetime import datetime

    _insert_bars(db, "600519", [Candle(datetime(2026, 11, 9), 1, 1, 1, 1, 10)])
    _insert_bars(db, "600519", [Candle(datetime(2026, 11, 9), 2, 2, 2, 2, 20)])
    db.commit()
    assert db.execute(select(Kline.close, Kline.volume)).all() == [(2.0, 20)]
