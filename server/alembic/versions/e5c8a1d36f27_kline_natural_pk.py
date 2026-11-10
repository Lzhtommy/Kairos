"""kline 改用自然主键 (code, period, ts)、去掉冗余索引；hot_rank_daily.date 改 DATE；
新增 fin_report_revision（财报更正留档）

kline 原先主键是自增 id，(code, period, ts) 只是普通索引：InnoDB 按 id 聚簇，按股取 K 线是
随机读，且库层不防重复。改成自然主键后同一只股票的 bar 物理连续，ix_kline_code 与
ix_kline_code_period_ts 都被主键覆盖，一并删除。

MySQL 上 kline 整表重建（约 1800 万行），在 2C2G RDS 上需要几十分钟，须在收盘后执行。

Revision ID: e5c8a1d36f27
Revises: d7b3f9a15c42
Create Date: 2026-11-10 14:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5c8a1d36f27'
down_revision: Union[str, None] = 'd7b3f9a15c42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    have = _indexes("kline")
    if bind.dialect.name == "mysql":
        # MySQL 的 DDL 不在事务里，中途失败会留下改了一半的结构：每步先看现状，可重入。
        # 一条 ALTER 只重建一次表；没有重复行（上线前已核对）
        # 冗余索引和主键一起改，免得重建表时白建一遍
        alters = [f"DROP INDEX {ix}" for ix in ("ix_kline_code", "ix_kline_code_period_ts") if ix in have]
        if "id" in {c["name"] for c in insp.get_columns("kline")}:
            alters = ["DROP PRIMARY KEY", "DROP COLUMN id", "ADD PRIMARY KEY (code, period, ts)", *alters]
        if alters:
            op.execute("ALTER TABLE kline " + ", ".join(alters))
        # hot_rank_daily 由应用启动时 create_all 建，全新库跑迁移时还不存在
        if insp.has_table("hot_rank_daily") and not isinstance(
            next(c["type"] for c in insp.get_columns("hot_rank_daily") if c["name"] == "date"), sa.Date
        ):
            op.execute("ALTER TABLE hot_rank_daily MODIFY date DATE NOT NULL")
    else:
        with op.batch_alter_table("kline", recreate="always") as b:
            for ix in ("ix_kline_code", "ix_kline_code_period_ts"):
                if ix in have:
                    b.drop_index(ix)
            b.drop_column("id")
            b.create_primary_key("pk_kline", ["code", "period", "ts"])
        # SQLite 的 Date 本来就存 'YYYY-MM-DD' 文本，与原列一致，hot_rank_daily 不用动

    if insp.has_table("fin_report_revision"):
        return
    op.create_table(
        'fin_report_revision',
        sa.Column('code', sa.String(16), nullable=False),
        sa.Column('report_date', sa.Date(), nullable=False),
        sa.Column('revised_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('notice_date', sa.Date(), nullable=True),
        sa.Column('eps', sa.Float(), nullable=True),
        sa.Column('deduct_eps', sa.Float(), nullable=True),
        sa.Column('revenue', sa.Float(), nullable=True),
        sa.Column('net_profit', sa.Float(), nullable=True),
        sa.Column('roe', sa.Float(), nullable=True),
        sa.Column('bps', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('code', 'report_date', 'revised_at'),
    )


def downgrade() -> None:
    op.drop_table('fin_report_revision')
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        if sa.inspect(bind).has_table("hot_rank_daily"):
            op.execute("ALTER TABLE hot_rank_daily MODIFY date VARCHAR(10) NOT NULL")
        op.execute("ALTER TABLE kline DROP PRIMARY KEY, "
                   "ADD COLUMN id INT NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST, "
                   "ADD INDEX ix_kline_code (code), "
                   "ADD INDEX ix_kline_code_period_ts (code, period, ts)")
    else:
        with op.batch_alter_table("kline", recreate="always") as b:
            b.drop_constraint("pk_kline", type_="primary")
            b.add_column(sa.Column("id", sa.Integer(), primary_key=True))
            b.create_index("ix_kline_code", ["code"])
            b.create_index("ix_kline_code_period_ts", ["code", "period", "ts"])
