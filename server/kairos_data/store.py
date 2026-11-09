"""写库工具：批量 upsert，以及 MySQL 下 Float / DateTime 的编译口径。"""

from __future__ import annotations

from sqlalchemy import DateTime, Float, Table
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session


# MySQL 方言默认把 Float 编译成 4 字节 FLOAT（约 7 位有效数字，市值 / 成交额会丢精度），
# DateTime 编译成不带小数秒的 DATETIME（微秒被四舍五入，可能进位到下一秒）。
# 统一成 SQLite 下的实际语义：双精度 + 微秒。
@compiles(Float, "mysql")
def _mysql_double(_type, _compiler, **_kw) -> str:  # noqa: ANN001
    return "DOUBLE"


@compiles(DateTime, "mysql")
def _mysql_datetime6(_type, _compiler, **_kw) -> str:  # noqa: ANN001
    return "DATETIME(6)"


def upsert_rows(db, table, rows: list[dict], batch: int = 1000) -> None:  # noqa: ANN001
    """按主键批量 upsert，每批一条语句。db 为 Session 或 Connection，table 为 Table 或 ORM 类。

    替代逐行 db.merge：merge 每行先 SELECT 再 UPDATE，RDS 一次往返约 5ms，
    全市场 5500 只一轮要一分钟以上。只适用于除主键外没有唯一键的表
    （MySQL 的 ON DUPLICATE KEY 对任一唯一键冲突都会触发更新）。
    """
    if not rows:
        return
    if not isinstance(table, Table):
        table = table.__table__
    pk = {c.name for c in table.primary_key.columns}
    dialect = (db.get_bind() if isinstance(db, Session) else db).dialect.name
    for i in range(0, len(rows), batch):
        chunk = rows[i : i + batch]
        cols = [c for c in chunk[0] if c not in pk]
        # 全部列都是主键（如指数成分）时没有可更新的列，冲突即跳过
        if dialect == "mysql":
            from sqlalchemy.dialects.mysql import insert as mysql_insert

            stmt = mysql_insert(table).values(chunk)
            stmt = (stmt.on_duplicate_key_update({c: stmt.inserted[c] for c in cols}) if cols
                    else stmt.prefix_with("IGNORE"))
        else:
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            stmt = sqlite_insert(table).values(chunk)
            stmt = (stmt.on_conflict_do_update(
                index_elements=sorted(pk), set_={c: stmt.excluded[c] for c in cols}
            ) if cols else stmt.on_conflict_do_nothing(index_elements=sorted(pk)))
        db.execute(stmt)
