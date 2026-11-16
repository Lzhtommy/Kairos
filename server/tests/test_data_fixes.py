"""2026-11 数据核对后的修复：科创板成交量单位、行业补缺、退市股财报与分红、指数更新容错、
行情表残行清理、指数成分权重回退。外部接口全部 monkeypatch。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from app.api import data as data_api
from app.core.db import get_db
from app.core.security import get_current_user
from app.jobs import collect
from app.models.market import Fundamental, Kline, Quote, StockInfo
from app.models.warehouse import IndexConstituent
from app.services.dsl import execute
from kairos_data import schema, sync
from kairos_data.sources import tencent
from kairos_data.sources.tencent import TencentProvider
from kairos_data.store import upsert_rows

# ---------------------------------------------------------------- 科创板成交量


def test_tencent_star_board_volume_is_normalised_to_lots():
    # 腾讯日 K：[date, open, close, high, low, volume]；科创板的 volume 是股，其余是手
    row = ["2026-11-13", "100", "101", "102", "99", "60681953.000"]
    assert tencent._parse_kline_rows([row], "sh688981")[0].volume == 606820
    assert tencent._parse_kline_rows([row], "sh689009")[0].volume == 606820
    assert tencent._parse_kline_rows([row], "sh600519")[0].volume == 60681953
    assert tencent._parse_kline_rows([row], "sz300750")[0].volume == 60681953
    assert tencent._parse_kline_rows([row])[0].volume == 60681953  # 指数等不带 symbol 的调用不变


def _quote_fields(code: str, volume: str) -> list[str]:
    f = [""] * 70
    f[2], f[3], f[4], f[30], f[36] = code, "10.0", "9.9", "20261116150000", volume
    return f


def test_tencent_quotes_star_board_volume(monkeypatch):
    p = TencentProvider()
    monkeypatch.setattr(p, "_fetch_quote_fields", lambda symbols: {
        "sh688981": _quote_fields("688981", "21699514"), "sh600519": _quote_fields("600519", "16725")})
    got = {q.code: q.volume for q in p.get_quotes(["688981", "600519"])}
    assert got == {"688981": 216995, "600519": 16725}


def _kbar(day: date, volume: int) -> dict:
    return {"code": "688981", "period": "1d", "ts": datetime(day.year, day.month, day.day),
            "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": volume}


def test_star_volume_fixes_uses_truth_then_own_history():
    days = [date(2026, 10, 1) + timedelta(days=i) for i in range(12)]
    truth = {d: 1_000_000.0 for d in days[:10]}  # 最后两天每日指标还没更新到
    bars = [_kbar(d, 1_000_000) for d in days[:8]]  # 前 8 天正确
    bars += [_kbar(days[8], 100_000_000), _kbar(days[9], 100_000_000)]  # 有对照、放大 100 倍
    bars += [_kbar(days[10], 120_000_000), _kbar(days[11], 3_000_000)]  # 无对照：一根放大，一根正常放量
    fixes = sync.star_volume_fixes(bars, truth)
    assert [(f["ts"].date(), f["volume"]) for f in fixes] == [
        (days[8], 1_000_000), (days[9], 1_000_000), (days[10], 1_200_000)]
    assert fixes[0]["close"] == 1.0  # 返回整行，upsert 不会把价格写空
    assert sync.star_volume_fixes([_kbar(d, 1_000_000) for d in days], truth) == []


def test_repair_star_volume_is_idempotent_and_leaves_other_boards(db):
    days = [date(2026, 11, 2) + timedelta(days=i) for i in range(5)]
    db.execute(insert(schema.kline), [_kbar(d, 5_000_000_00) for d in days])  # 全历史放大
    db.execute(insert(schema.kline), [{**_kbar(d, 5_000_000_00), "code": "600519"} for d in days])
    upsert_rows(db, schema.daily_basic, [
        {"code": "688981", "trade_date": d, "volume": 5_000_000.0, "suspended": False, "is_st": False}
        for d in days])
    db.commit()
    assert sync.repair_star_volume(db, fetch_truth=lambda code: {}) == (5, 1)
    vols = dict(db.execute(select(schema.kline.c.code, schema.kline.c.volume)
                           .where(schema.kline.c.ts == datetime(2026, 11, 2))).all())
    assert vols == {"688981": 5_000_000, "600519": 5_000_000_00}
    assert sync.repair_star_volume(db, fetch_truth=lambda code: {}) == (0, 0)


def test_repair_star_volume_falls_back_when_no_daily_basic(db):
    day = date(2026, 11, 13)
    db.execute(insert(schema.kline), [{**_kbar(day, 3_000_000_00), "code": "689009"}])
    db.commit()
    assert sync.repair_star_volume(db, fetch_truth=lambda code: {day: 3_000_000.0}) == (1, 1)

    def boom(code):
        raise RuntimeError("限流")

    assert sync.repair_star_volume(db, fetch_truth=boom) == (0, 0)  # 取不到对照就不动


# ---------------------------------------------------------------- 行业


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_industry_map_supplements_only_missing_codes(monkeypatch):
    p = TencentProvider()

    def fake_get(url, **kw):
        if "mktHs/rank" in url:
            return _Resp({"data": [{"bd_name": "半导体Ⅱ", "bd_code": "pt01801081"}]})
        return _Resp({"data": {"rank_list": [{"code": "sz300661"}], "total": 1}})

    monkeypatch.setattr(p, "_get", fake_get)
    seen = {}

    def fake_sw(boards):
        seen.update(boards)
        return {"688981": "半导体", "300661": "别的名字"}

    monkeypatch.setattr(p, "_sw_members", fake_sw)
    assert p._industry_map() == {"300661": "半导体"}  # 默认不去申万官网
    assert p._industry_map(supplement=True) == {"300661": "半导体", "688981": "半导体"}
    assert seen == {"801081": "半导体"}


def _row(code: str, industry: str, pe: float, roe: float = 10.0) -> dict:
    return {"code": code, "name": code, "market": "SH", "industry": industry, "pe": pe, "roe": roe}


def test_stocks_without_industry_are_not_one_peer_group():
    rows = [_row("A", "白酒", 5), _row("B", "白酒", 10), _row("C", "白酒", 20),
            _row("X", "—", 5), _row("Y", "—", 50), _row("Z", "—", 500)]
    dsl = {"filters": [{"factor": "pe", "op": "lt", "ref": "industry_median"}], "technical": []}
    assert [r["code"] for r in execute(dsl, rows)] == ["A"]  # X 不再因为低于「—」的中位数而命中
    dsl = {"filters": [{"factor": "roe", "op": "rank_top", "value": 1, "scope": "industry"}], "technical": []}
    rows = [_row("A", "白酒", 5, roe=20), _row("B", "白酒", 5, roe=10), _row("X", "—", 5, roe=99)]
    assert [r["code"] for r in execute(dsl, rows)] == ["A"]


# ---------------------------------------------------------------- 退市股财报与分红


def _delisted(db) -> None:
    db.execute(insert(schema.stock_info), [
        {"code": "300104", "name": "乐视退", "market": "SZ", "industry": "—",
         "list_date": date(2010, 8, 12), "delist_date": date(2020, 7, 21)},
        {"code": "600087", "name": "退市长油", "market": "SH", "industry": "—",
         "list_date": date(1997, 6, 12), "delist_date": date(2014, 6, 5)},
        {"code": "600519", "name": "贵州茅台", "market": "SH", "industry": "白酒",
         "list_date": date(2001, 8, 27), "delist_date": None},
    ])
    db.commit()


def test_backfill_delisted_financials(db, monkeypatch):
    _delisted(db)
    db.execute(insert(schema.fin_report), [{"code": "600087", "report_date": date(2012, 12, 31),
                                            "updated_at": datetime(2026, 1, 1)}])
    db.commit()
    asked = []

    def fake(report, filter="", **_kw):
        asked.append((report, filter))
        return [{"SECURITY_CODE": "300104", "REPORT_DATE": "2016-12-31 00:00:00",
                 "NOTICE_DATE": "2017-04-20 00:00:00", "EPSJB": 0.1421, "EPSKCJB": 0.29,
                 "TOTALOPERATEREVE": 2.2e10, "PARENTNETPROFIT": 5.5e8, "TOTALOPERATEREVETZ": 68.9,
                 "PARENTNETPROFITTZ": -3.19, "ROEJQ": 8.2, "BPS": 5.16, "MGJYXJJE": -0.54, "XSMLL": 16.95},
                {"SECURITY_CODE": "300104", "REPORT_DATE": None}]

    monkeypatch.setattr(sync.eastmoney_dc, "fetch_report", fake)
    assert sync.backfill_delisted_financials(db, throttle=0) == (1, 0)
    # 只问没有财报的退市股：在市的茅台、已有财报的长油都不问
    assert asked == [("RPT_F10_FINANCE_MAINFINADATA", '(SECUCODE="300104.SZ")')]
    r = db.execute(select(schema.fin_report).where(schema.fin_report.c.code == "300104")).mappings().one()
    assert (r["report_date"], r["notice_date"], r["roe"], r["net_profit"], r["deduct_eps"]) == (
        date(2016, 12, 31), date(2017, 4, 20), 8.2, 5.5e8, 0.29)
    assert sync.backfill_delisted_financials(db, throttle=0) == (0, 0)  # 续跑不重复拉


def test_delisted_dividend_rows_maps_report_period_and_per10():
    raw = [
        {"dividPlanAnnounceDate": "2024-04-03", "dividPlanDate": "2024-06-12", "dividRegistDate": "2024-06-18",
         "dividOperateDate": "2024-06-19", "dividCashPsBeforeTax": "30.876", "dividStocksPs": "0.000000",
         "dividReserveToStockPs": "", "dividCashStock": "10派308.76元"},
        {"dividPlanAnnounceDate": "2024-11-09", "dividPlanDate": "2024-12-14", "dividRegistDate": "2024-12-19",
         "dividOperateDate": "2024-12-20", "dividCashPsBeforeTax": "23.882", "dividStocksPs": "0.000000",
         "dividReserveToStockPs": "1.200000", "dividCashStock": "10转12派238.82元"},
        {"dividPlanAnnounceDate": "2024-12-01", "dividOperateDate": "2025-01-10", "dividCashPsBeforeTax": "1"},
        {"dividPlanAnnounceDate": "2025-03-01", "dividOperateDate": ""},  # 还没实施
    ]
    rows = {r["report_date"]: r for r in sync.delisted_dividend_rows("600519", raw)}
    assert sorted(rows) == [date(2023, 12, 31), date(2024, 6, 30), date(2024, 9, 30)]  # 同期第二笔顺延
    a = rows[date(2023, 12, 31)]
    assert (a["ex_date"], a["record_date"], a["cash_per10"], a["transfer_per10"]) == (
        date(2024, 6, 19), date(2024, 6, 18), 308.76, None)
    assert rows[date(2024, 6, 30)]["transfer_per10"] == 12.0 and rows[date(2024, 6, 30)]["cash_per10"] == 238.82


def test_backfill_delisted_dividends(db, monkeypatch):
    _delisted(db)

    class _Session:
        def __enter__(self):
            return object()

        def __exit__(self, *a):
            return False

    years = []

    def fake(bs, symbol, year):
        years.append((symbol, year))
        if (symbol, year) == ("sz.300104", 2016):
            return [{"dividPlanAnnounceDate": "2016-03-18", "dividOperateDate": "2016-05-03",
                     "dividCashPsBeforeTax": "0.031", "dividCashStock": "10派0.31元"}]
        if symbol == "sh.600087":
            raise RuntimeError("网络错误")
        return []

    monkeypatch.setattr(sync.baostock, "session", lambda: _Session())
    monkeypatch.setattr(sync.baostock, "fetch_dividends", fake)
    assert sync.backfill_delisted_dividends(db, throttle=0) == (1, 1)
    assert [y for s, y in years if s == "sz.300104"] == list(range(2010, 2021))  # 上市年到退市年
    r = db.execute(select(schema.dividend)).mappings().one()
    assert (r["code"], r["report_date"], r["ex_date"], r["cash_per10"]) == (
        "300104", date(2015, 12, 31), date(2016, 5, 3), 0.31)


def test_backfill_parts_marked_done_only_when_nothing_failed(db, monkeypatch):
    assert {"delistfin", "delistdiv", "starvol"} <= set(sync.missing_parts(db))
    monkeypatch.setattr(sync, "backfill_delisted_financials", lambda db: (3, 1))
    monkeypatch.setattr(sync, "backfill_delisted_dividends", lambda db: (7, 0))
    monkeypatch.setattr(sync, "repair_star_volume", lambda db: (0, 0))
    sync.backfill(db, ("delistfin", "delistdiv", "starvol"))
    missing = set(sync.missing_parts(db))
    assert "delistfin" in missing and not {"delistdiv", "starvol"} & missing


# ---------------------------------------------------------------- 指数更新容错


def test_index_bars_one_failure_does_not_skip_the_rest(db, monkeypatch):
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)
    calls = []

    def his(secid):
        calls.append(secid)
        if secid == "1.000300":
            raise RuntimeError("RemoteDisconnected")
        return [{"date": "2026-11-13", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10, "amount": 100}]

    monkeypatch.setattr(sync.eastmoney_dc, "fetch_his_kline", his)
    assert sync.refresh_index_bars(db) == len(sync.INDEXES) - 1
    have = set(db.execute(select(schema.index_bar.c.index_code)).scalars())
    assert have == set(sync.INDEXES) - {"000300"}
    assert calls.count("1.000300") == 2  # 末尾重试过一次

    def always(secid):
        raise RuntimeError("down")

    monkeypatch.setattr(sync.eastmoney_dc, "fetch_his_kline", always)
    with pytest.raises(RuntimeError):
        sync.refresh_index_bars(db)  # 全部失败要让外层记为失败


def test_index_bars_retry_recovers(db, monkeypatch):
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)
    failed_once = set()

    def his(secid):
        if secid not in failed_once and secid in ("1.000905", "2.932000"):
            failed_once.add(secid)
            raise RuntimeError("RemoteDisconnected")
        return [{"date": "2026-11-13", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10, "amount": 100}]

    monkeypatch.setattr(sync.eastmoney_dc, "fetch_his_kline", his)
    assert sync.refresh_index_bars(db) == len(sync.INDEXES)


# ---------------------------------------------------------------- 行情表残行


def _quote(code: str) -> Quote:
    return Quote(code=code, ts=datetime.now(timezone.utc), price=1, prev_close=1, open=1, high=1, low=1,
                 volume=0, turnover=0, turnover_rate=0, pe=None, pb=1, market_cap=1, roe=0)


def test_prune_stale_quotes_and_renumbered_bj_codes(db, monkeypatch):
    monkeypatch.setattr(collect, "_PRUNE_MIN_UNIVERSE", 2)
    monkeypatch.setattr(collect, "_PRUNE_MAX_SHARE", 0.9)
    old = datetime.now() - timedelta(days=100)
    recent = datetime.now() - timedelta(days=3)
    db.add_all([
        StockInfo(code="600519", name="贵州茅台", market="SH", industry="白酒"),
        StockInfo(code="920799", name="艾融软件", market="BJ", industry="—"),
        StockInfo(code="830799", name="艾融软件", market="BJ", industry="—"),  # 改号前的旧代码，没有 K 线
        StockInfo(code="920305", name="云创退", market="BJ", industry="—"),  # 已停更
        StockInfo(code="920001", name="停牌中", market="BJ", industry="—"),  # 不在股票池但 K 线还新
        StockInfo(code="600001", name="邯郸钢铁", market="SH", industry="—", delist_date=date(2009, 12, 29)),
        _quote("600519"), _quote("920799"), _quote("830799"), _quote("920305"), _quote("600001"),
        Fundamental(code="830799", pe=1, pb=1, roe=1, market_cap=1, dividend_yield=0),
        Kline(code="920305", period="1d", ts=datetime(old.year, old.month, old.day),
              open=1, high=1, low=1, close=1, volume=1),
        Kline(code="920001", period="1d", ts=datetime(recent.year, recent.month, recent.day),
              open=1, high=1, low=1, close=1, volume=1),
    ])
    db.commit()
    assert collect._prune_stale(db, {"600519", "920799"}) == (3, 1, 1)
    assert set(db.execute(select(Quote.code)).scalars()) == {"600519", "920799"}
    assert db.execute(select(Fundamental.code)).scalars().all() == []
    infos = {s.code: s for s in db.execute(select(StockInfo)).scalars()}
    assert "830799" not in infos  # 旧代码连资料行一起删
    assert infos["920305"].delist_date == (old + timedelta(days=1)).date()
    assert infos["920001"].delist_date is None and infos["600001"].delist_date == date(2009, 12, 29)


def test_prune_stale_skips_untrustworthy_universe(db):
    db.add_all([_quote("600519"), _quote("000001")])
    db.commit()
    assert collect._prune_stale(db, {"600519"}) == (0, 0, 0)  # 股票池太小：数据源被截断
    live = {f"{i:06d}" for i in range(5000)}
    assert collect._prune_stale(db, live) == (0, 0, 0)  # 库里的行情全不在池里：不可信
    assert len(db.execute(select(Quote.code)).scalars().all()) == 2


# ---------------------------------------------------------------- 指数成分权重回退


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(data_api.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: object()
    return TestClient(app)


def test_index_members_borrow_weights_from_latest_weighted_snapshot(client, db):
    upsert_rows(db, IndexConstituent, [
        {"index_code": "000300", "snapshot_date": date(2026, 10, 30), "code": "000001", "weight": 0.5},
        {"index_code": "000300", "snapshot_date": date(2026, 10, 30), "code": "600519", "weight": 4.2},
    ])
    upsert_rows(db, IndexConstituent, [  # BaoStock 的新名单：无权重，调入了 300750
        {"index_code": "000300", "snapshot_date": date(2026, 11, 9), "code": c}
        for c in ("000001", "600519", "300750")])
    db.commit()
    d = client.get("/data/index/000300").json()
    assert (d["snapshotDate"], d["weightDate"]) == ("2026-11-09", "2026-10-30")
    assert [(m["code"], m["weight"]) for m in d["members"]] == [
        ("600519", 4.2), ("000001", 0.5), ("300750", None)]
