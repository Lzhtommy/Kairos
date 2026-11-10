"""block_trade 改用业务主键 (code, trade_date, seq)，去掉自增 id

同一对买卖方常把大单拆成价量完全相同的多笔（生产上约 1 万组），没有天然唯一键；seq 为同股同日
按 (价格, 数量, 金额, 买方, 卖方) 排序后的序号。整表约 34 万行，复制重建，秒级完成。

Revision ID: f8d2b6c41a93
Revises: e5c8a1d36f27
Create Date: 2026-11-10 18:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f8d2b6c41a93'
down_revision: Union[str, None] = 'e5c8a1d36f27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLS = "code, trade_date, price, premium_pct, volume, amount, buyer, seller"


def _create(name: str) -> None:
    op.create_table(
        name,
        sa.Column('code', sa.String(16), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False, autoincrement=False),
        sa.Column('price', sa.Float(), nullable=True),
        sa.Column('premium_pct', sa.Float(), nullable=True),
        sa.Column('volume', sa.BigInteger(), nullable=True),
        sa.Column('amount', sa.Float(), nullable=True),
        sa.Column('buyer', sa.String(128), nullable=False),
        sa.Column('seller', sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint('code', 'trade_date', 'seq'),
    )


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    # 可重入（MySQL 的 DDL 不在事务里）：旧表已删、新表没改名时接着改名；已是新结构就跳过；
    # 复制中途失败留下的临时表先清掉
    if not insp.has_table("block_trade") and insp.has_table("block_trade_new"):
        op.rename_table("block_trade_new", "block_trade")
        op.create_index("ix_block_trade_trade_date", "block_trade", ["trade_date"])
        return
    if "seq" in {c["name"] for c in insp.get_columns("block_trade")}:
        return
    if insp.has_table("block_trade_new"):
        op.drop_table("block_trade_new")
    _create("block_trade_new")
    op.execute(
        f"INSERT INTO block_trade_new (code, trade_date, seq, price, premium_pct, volume, amount, buyer, seller) "
        f"SELECT code, trade_date, ROW_NUMBER() OVER (PARTITION BY code, trade_date ORDER BY "
        f"price IS NULL, price, COALESCE(volume, 0), COALESCE(amount, 0), buyer, seller), "
        f"price, premium_pct, volume, amount, buyer, seller FROM block_trade"
    )
    op.drop_table("block_trade")
    op.rename_table("block_trade_new", "block_trade")
    op.create_index("ix_block_trade_trade_date", "block_trade", ["trade_date"])


def downgrade() -> None:
    op.create_table(
        'block_trade_old',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('code', sa.String(16), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('price', sa.Float(), nullable=True),
        sa.Column('premium_pct', sa.Float(), nullable=True),
        sa.Column('volume', sa.BigInteger(), nullable=True),
        sa.Column('amount', sa.Float(), nullable=True),
        sa.Column('buyer', sa.String(128), nullable=False),
        sa.Column('seller', sa.String(128), nullable=False),
    )
    op.execute(f"INSERT INTO block_trade_old ({_COLS}) SELECT {_COLS} FROM block_trade ORDER BY trade_date, code, seq")
    op.drop_table("block_trade")
    op.rename_table("block_trade_old", "block_trade")
    op.create_index("ix_block_trade_code", "block_trade", ["code"])
    op.create_index("ix_block_trade_trade_date", "block_trade", ["trade_date"])
