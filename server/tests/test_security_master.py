from datetime import date

from sqlalchemy import select

from app.jobs.security_master import sync
from app.models.market import SecurityMaster, StockInfo


def _raw(code: str, name: str, ipo: str, out: str, typ: str, status: str) -> dict:
    return {"code": code, "code_name": name, "ipoDate": ipo, "outDate": out,
            "type": typ, "status": status}


def _all(db) -> dict:
    return {(r.exchange, r.code): r for r in db.execute(select(SecurityMaster)).scalars()}


def test_sync_keeps_delisted_and_separates_exchanges(db):
    db.add(StockInfo(code="920000", name="安徽凤凰", market="BJ", industry="—"))
    db.add(StockInfo(code="832317", name="观典防务", market="SZ", industry="—"))  # 腾讯源标错
    db.add(StockInfo(code="600519", name="贵州茅台", market="SH", industry="白酒"))
    db.commit()
    n = sync(db, [
        _raw("sh.600519", "贵州茅台", "2001-08-27", "", "1", "1"),
        _raw("sh.600001", "邯郸钢铁", "1998-01-22", "2009-12-29", "1", "0"),
        _raw("sh.000001", "上证综合指数", "1991-07-15", "", "2", "1"),
        _raw("sz.000001", "平安银行", "1991-04-03", "", "1", "1"),
    ])
    assert n == 6
    rows = _all(db)
    gang = rows[("SH", "600001")]
    assert (gang.status, gang.delist_date) == ("delisted", date(2009, 12, 29))
    # 同代码的指数和股票靠交易所区分
    assert rows[("SH", "000001")].sec_type == "index"
    assert rows[("SZ", "000001")].sec_type == "stock"
    # BaoStock 没有的北交所由股票池补齐
    bj = rows[("BJ", "920000")]
    assert (bj.source, bj.status, bj.list_date) == ("kairos", "listed", None)
    assert ("SZ", "832317") not in rows and rows[("BJ", "832317")].source == "kairos"
    assert rows[("SH", "600519")].source == "baostock"


def test_sync_updates_status_and_ignores_empty(db):
    sync(db, [_raw("sz.000002", "万科A", "1991-01-29", "", "1", "1")])
    assert sync(db, []) == 0
    sync(db, [_raw("sz.000002", "*ST万科", "1991-01-29", "2026-11-01", "1", "0")])
    r = _all(db)[("SZ", "000002")]
    assert (r.name, r.status, r.delist_date) == ("*ST万科", "delisted", date(2026, 11, 1))
