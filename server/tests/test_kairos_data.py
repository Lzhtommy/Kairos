"""独立采集包 kairos_data：命令行建表、股票池、日 K 整段替换与补到上市日。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime

from sqlalchemy import create_engine, insert, inspect, select

from kairos_data import cli, schema, sync
from kairos_data.sources.base import Candle
from kairos_data.sync import replace_kline, sync_listing


def test_cli_init_creates_tables(tmp_path):
    url = f"sqlite:///{tmp_path / 'kd.db'}"
    assert cli.main(["init", "--db", url]) == 0
    tables = set(inspect(create_engine(url)).get_table_names())
    assert tables == set(schema.metadata.tables)


def test_standalone_listing_adds_listed_stocks(db):
    raw = [
        {"code": "sh.600519", "code_name": "贵州茅台", "ipoDate": "2001-08-27", "outDate": "",
         "type": "1", "status": "1"},
        {"code": "sh.600001", "code_name": "邯郸钢铁", "ipoDate": "1998-01-22", "outDate": "2009-12-29",
         "type": "1", "status": "0"},
    ]
    sync_listing(db, raw, add_listed=False)
    assert set(db.execute(select(schema.stock_info.c.code)).scalars()) == {"600001"}
    sync_listing(db, raw)
    rows = {r["code"]: r for r in db.execute(select(schema.stock_info)).mappings()}
    assert rows["600519"]["list_date"] == date(2001, 8, 27) and rows["600519"]["delist_date"] is None


def _bar(day: str, close: float) -> dict:
    return {"period": "1d", "ts": datetime.fromisoformat(day), "open": close, "high": close,
            "low": close, "close": close, "volume": 100}


def _klines(db, code: str) -> list[tuple]:
    return db.execute(select(schema.kline.c.ts, schema.kline.c.close)
                      .where(schema.kline.c.code == code).order_by(schema.kline.c.ts)).all()


def test_replace_kline_keeps_newer_bars_and_guards_short(db):
    db.execute(insert(schema.kline), [{"code": "600000", **_bar(d, 9.0)}
                                      for d in ("2026-11-05", "2026-11-06", "2026-11-09")])
    db.commit()
    # 新数据只到 11-06：11-09 的当日 bar 保留
    assert replace_kline(db, "600000", [_bar("2026-11-04", 1.0), _bar("2026-11-05", 2.0),
                                        _bar("2026-11-06", 3.0)]) == 4
    assert [c for _, c in _klines(db, "600000")] == [1.0, 2.0, 3.0, 9.0]
    # 明显比库里短：不覆盖
    assert replace_kline(db, "600000", [_bar("2026-11-06", 5.0)]) == 0
    assert len(_klines(db, "600000")) == 4


class _FlakyTencent:
    def __init__(self):
        self.calls = []

    def get_kline_history(self, code, bars):
        self.calls.append(code)
        if code == "600001":
            raise RuntimeError("501")
        return [Candle(datetime(1999, 1, 4), 1, 1, 1, 1, 10), Candle(datetime(2026, 11, 6), 2, 2, 2, 2, 10)]


def test_backfill_history_skips_complete_and_falls_back(db, monkeypatch):
    db.execute(insert(schema.stock_info), [
        {"code": "600000", "name": "浦发", "market": "SH", "industry": "银行", "list_date": date(1999, 1, 4)},
        {"code": "600001", "name": "甲", "market": "SH", "industry": "—", "list_date": date(1999, 1, 4)},
        {"code": "600002", "name": "乙", "market": "SH", "industry": "—", "list_date": date(1999, 1, 4)},
        {"code": "600003", "name": "已补齐", "market": "SH", "industry": "—", "list_date": date(2020, 1, 2)},
        {"code": "920001", "name": "北交所", "market": "BJ", "industry": "—", "list_date": None},
    ])
    db.execute(insert(schema.kline), [{"code": "600003", **_bar("2020-01-02", 1.0)},
                                      {"code": "600000", **_bar("2026-11-06", 1.0)}])
    db.commit()

    @contextmanager
    def fake_session():
        yield object()

    monkeypatch.setattr(sync.baostock, "session", fake_session)
    monkeypatch.setattr(sync.baostock, "fetch_daily_qfq", lambda bs, symbol: [
        {"date": "1999-01-04", "open": "1", "high": "1", "low": "1", "close": "1", "volume": "1000",
         "tradestatus": "1"}])
    tencent = _FlakyTencent()
    assert sync.backfill_history(db, tencent, throttle=0) == (3, 0, 0)
    # 600001 腾讯失败后，冷却期内 600002 也直接走 BaoStock
    assert tencent.calls == ["600000", "600001"]
    assert _klines(db, "600000")[0][0] == datetime(1999, 1, 4)
    assert _klines(db, "600002")[0][0] == datetime(1999, 1, 4)
    assert sync.history_gaps(db) == []


def test_holder_parsers_filter_non_a_and_convert_units():
    raw = [
        {"SECUCODE": "603926.SH", "SECURITY_CODE": "603926", "END_DATE": "2026-09-30 00:00:00",
         "HOLDER_RANK": 1, "HOLDER_NAME": "杭州德萨", "HOLDER_TYPE": "其它", "SHARES_TYPE": "A股",
         "HOLD_NUM": 81302242, "FREE_HOLDNUM_RATIO": 35.22, "HOLD_NUM_CHANGE": "不变",
         "UPDATE_DATE": "2026-10-28 00:00:00"},
        {"SECUCODE": "900906.SH", "SECURITY_CODE": "900906", "END_DATE": "2026-09-30 00:00:00",
         "HOLDER_RANK": 1, "HOLDER_NAME": "B 股"},
        {"SECUCODE": "875004.NQ", "SECURITY_CODE": "875004", "END_DATE": "2026-09-30 00:00:00",
         "HOLDER_RANK": 1, "HOLDER_NAME": "新三板"},
        {"SECUCODE": "920992.BJ", "SECURITY_CODE": "920992", "END_DATE": "2026-09-30 00:00:00",
         "HOLDER_RANK": 1, "HOLDER_NAME": "北交所"},
    ]
    rows = sync.top_holder_rows(raw, "float")
    assert [r["code"] for r in rows] == ["603926", "920992"]
    assert rows[0]["hold_ratio"] == 35.22 and rows[0]["notice_date"] == date(2026, 10, 28)
    lock = sync.lockup_rows([{
        "SECUCODE": "600367.SH", "SECURITY_CODE": "600367", "FREE_DATE": "2026-11-02 00:00:00",
        "FREE_SHARES_TYPE": "定向增发机构配售股份", "BATCH_HOLDER_NUM": 1, "CURRENT_FREE_SHARES": 1915.772,
        "ABLE_FREE_SHARES": 1915.772, "LIFT_MARKET_CAP": 50633.85, "FREE_RATIO": 0.0561, "TOTAL_RATIO": 0.0561,
    }])[0]
    assert round(lock["free_shares"]) == 19157720 and round(lock["float_ratio"], 2) == 5.61


def test_top_holders_fill_notice_date_from_float(db, monkeypatch):
    def fake(report, filter="", sort="", **_kw):
        assert sort  # 东财不排序时翻页不稳定
        base = {"SECUCODE": "600519.SH", "SECURITY_CODE": "600519", "END_DATE": "2026-09-30 00:00:00",
                "HOLDER_RANK": 1, "HOLDER_NAME": "茅台集团", "HOLD_NUM": 681282935}
        if report == "RPT_F10_EH_FREEHOLDERS":
            return [{**base, "UPDATE_DATE": "2026-10-31 00:00:00", "FREE_HOLDNUM_RATIO": 54.49}]
        return [{**base, "HOLD_NUM_RATIO": 54.5}]

    monkeypatch.setattr(sync.eastmoney_dc, "fetch_report", fake)
    assert sync.refresh_top_holders(db, [date(2026, 9, 30)]) == 2
    rows = {r["kind"]: r for r in db.execute(select(schema.top_holder)).mappings()}
    assert rows["all"]["notice_date"] == date(2026, 10, 31) and rows["all"]["hold_ratio"] == 54.5


def test_month_ranges():
    assert sync.month_ranges(date(2026, 9, 15), date(2026, 11, 9)) == [
        (date(2026, 11, 1), date(2026, 11, 9)), (date(2026, 10, 1), date(2026, 10, 31)),
        (date(2026, 9, 1), date(2026, 9, 30))]
