"""日线 ts 统一为 DATE（kline / factor_history / sector_bar / sector_metric / sector_valuation）；
kline 成交量由"手"改为"股"，与数据仓库各表一致

上线前已核对生产库：这几张表的 ts 全部是零点，改 DATE 不丢信息。Python 侧经 TradeDay 仍得到
零点 datetime，调用方不变。

MySQL 上 kline（约 1800 万行）复制到新表时一并换算，再用一条 RENAME TABLE 原子替换，只重建一次；
DDL 不在事务里，每步先看现状，中途失败可直接重跑。需在收盘后执行。

Revision ID: a9e4c7f20b58
Revises: f8d2b6c41a93
Create Date: 2026-11-10 20:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a9e4c7f20b58'
down_revision: Union[str, None] = 'f8d2b6c41a93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 由应用启动时 create_all 建的表，全新库跑迁移时可能还不存在
_DAILY_TABLES = ("factor_history", "sector_bar", "sector_metric", "sector_valuation")
_KLINE_COLS = "code, period, ts, open, high, low, close, volume"


def _ts_is_date(insp, table: str) -> bool:  # noqa: ANN001
    col = next(c for c in insp.get_columns(table) if c["name"] == "ts")
    return isinstance(col["type"], sa.Date) and not isinstance(col["type"], sa.DateTime)


def _mysql_kline(ts_type: str, ts_expr: str, vol_expr: str, done) -> None:  # noqa: ANN001
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("kline_old"):  # 上次已原子替换，只差删旧表
        if done(insp):
            op.drop_table("kline_old")
            return
    if done(insp):
        return
    if insp.has_table("kline_new"):  # 上次复制到一半
        op.drop_table("kline_new")
    op.execute(
        f"CREATE TABLE kline_new (code VARCHAR(16) NOT NULL, period VARCHAR(8) NOT NULL, ts {ts_type} NOT NULL, "
        "open DOUBLE NOT NULL, high DOUBLE NOT NULL, low DOUBLE NOT NULL, close DOUBLE NOT NULL, "
        "volume BIGINT NOT NULL, PRIMARY KEY (code, period, ts), KEY ix_kline_ts (ts)) "
        "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"
    )
    op.execute(f"INSERT INTO kline_new ({_KLINE_COLS}) "
               f"SELECT code, period, {ts_expr}, open, high, low, close, {vol_expr} FROM kline")
    op.execute("RENAME TABLE kline TO kline_old, kline_new TO kline")
    op.drop_table("kline_old")


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if bind.dialect.name == "mysql":
        _mysql_kline("DATE", "DATE(ts)", "volume * 100", lambda i: _ts_is_date(i, "kline"))
        for t in _DAILY_TABLES:
            if insp.has_table(t) and not _ts_is_date(insp, t):
                op.execute(f"ALTER TABLE {t} MODIFY ts DATE NOT NULL")
    else:
        # SQLite 的列类型只是亲和性，改存 'YYYY-MM-DD' 文本即可（batch 重建会 CAST AS DATE 成数字）；
        # SQLite 迁移在事务里，整体原子
        op.execute("UPDATE kline SET ts = date(ts), volume = volume * 100")
        for t in _DAILY_TABLES:
            if insp.has_table(t):
                op.execute(f"UPDATE {t} SET ts = date(ts)")


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if bind.dialect.name == "mysql":
        _mysql_kline("DATETIME(6)", "TIMESTAMP(ts)", "volume DIV 100", lambda i: not _ts_is_date(i, "kline"))
        for t in _DAILY_TABLES:
            if insp.has_table(t) and _ts_is_date(insp, t):
                op.execute(f"ALTER TABLE {t} MODIFY ts DATETIME(6) NOT NULL")
    else:
        op.execute("UPDATE kline SET ts = datetime(ts), volume = volume / 100")
        for t in _DAILY_TABLES:
            if insp.has_table(t):
                op.execute(f"UPDATE {t} SET ts = datetime(ts)")
