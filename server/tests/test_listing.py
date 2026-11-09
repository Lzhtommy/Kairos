from datetime import date

from sqlalchemy import select

from kairos_data.sync import sync_listing as sync
from app.models.market import StockInfo


def _raw(code: str, name: str, ipo: str, out: str, typ: str = "1", status: str = "1") -> dict:
    return {"code": code, "code_name": name, "ipoDate": ipo, "outDate": out,
            "type": typ, "status": status}


def _all(db) -> dict:
    db.expire_all()
    return {s.code: s for s in db.execute(select(StockInfo)).scalars()}


def test_sync_fills_dates_and_adds_delisted(db):
    db.add_all([
        StockInfo(code="600519", name="贵州茅台", market="SH", industry="白酒"),
        StockInfo(code="832317", name="观典防务", market="SZ", industry="—"),  # 腾讯源标错
    ])
    db.commit()
    sync(db, [
        _raw("sh.600519", "贵州茅台(旧名)", "2001-08-27", ""),
        _raw("sh.600001", "邯郸钢铁", "1998-01-22", "2009-12-29", status="0"),
        _raw("sh.000001", "上证综合指数", "1991-07-15", "", typ="2"),
        _raw("sh.900901", "云赛B股", "1992-02-21", ""),
    ])
    rows = _all(db)
    assert set(rows) == {"600519", "832317", "600001"}  # 指数、B 股不进股票池
    mt = rows["600519"]
    # 在市股票只补日期，名称 / 行业仍以现有为准
    assert (mt.name, mt.industry, mt.list_date, mt.delist_date) == (
        "贵州茅台", "白酒", date(2001, 8, 27), None)
    gang = rows["600001"]
    assert (gang.market, gang.industry, gang.delist_date) == ("SH", "—", date(2009, 12, 29))
    assert rows["832317"].market == "BJ"
    listed = set(db.execute(select(StockInfo.code).where(StockInfo.listed())).scalars())
    assert listed == {"600519", "832317"}


def test_sync_empty_writes_nothing(db):
    db.add(StockInfo(code="000002", name="万科A", market="SZ", industry="房地产"))
    db.commit()
    assert sync(db, []) == 0
    assert _all(db)["000002"].list_date is None
