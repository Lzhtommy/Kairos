"""SQLite 备份：快照可解压还原、数据一致、只保留最近 N 份。"""

from __future__ import annotations

import gzip
import sqlite3

from app.jobs.backup import backup_database


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO t (v) VALUES (?)", [("a",), ("b",), ("c",)])
    conn.commit()
    return conn


def test_backup_restores_same_rows(tmp_path):
    conn = _make_db(tmp_path / "src.db")  # 保持连接打开：WAL 未 checkpoint 的数据也要进快照
    out = backup_database(str(tmp_path / "src.db"), str(tmp_path / "bk"), keep=3)
    conn.close()

    assert out is not None and out.name.endswith(".db.gz")
    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(out.read_bytes()))
    rows = sqlite3.connect(restored).execute("SELECT v FROM t ORDER BY id").fetchall()
    assert rows == [("a",), ("b",), ("c",)]
    assert not list((tmp_path / "bk").glob("*.tmp"))


def test_backup_prunes_old_copies(tmp_path):
    _make_db(tmp_path / "src.db").close()
    bk = tmp_path / "bk"
    bk.mkdir()
    for day in ("20260101", "20260102", "20260103"):
        (bk / f"kairos-{day}-030000.db.gz").write_bytes(b"old")

    out = backup_database(str(tmp_path / "src.db"), str(bk), keep=2)

    left = sorted(p.name for p in bk.glob("kairos-*.db.gz"))
    assert len(left) == 2
    assert left[-1] == out.name
    assert left[0] == "kairos-20260103-030000.db.gz"


def test_backup_failure_returns_none(tmp_path):
    out = backup_database(str(tmp_path / "missing" / "x.db"), str(tmp_path / "bk"), keep=2)
    assert out is None
    assert not list((tmp_path / "bk").iterdir())
