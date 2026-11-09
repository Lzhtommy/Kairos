"""独立采集包 kairos_data：命令行建表、独立使用时股票池写入在市股票。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine, inspect, select

from kairos_data import cli, schema
from kairos_data.sync import sync_listing


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
