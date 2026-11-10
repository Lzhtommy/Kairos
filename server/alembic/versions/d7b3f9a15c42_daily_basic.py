"""daily_basic：每日指标（ST / 停牌 / 换手 / 估值 / 总市值）

Revision ID: d7b3f9a15c42
Revises: c3e5a7b92f16
Create Date: 2026-11-10 10:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd7b3f9a15c42'
down_revision: Union[str, None] = 'c3e5a7b92f16'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 与 kairos_data.schema 一致；Float 在 MySQL 下编译为 DOUBLE
    op.create_table(
        'daily_basic',
        sa.Column('code', sa.String(16), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('close', sa.Float(), nullable=True),
        sa.Column('preclose', sa.Float(), nullable=True),
        sa.Column('volume', sa.Float(), nullable=True),
        sa.Column('amount', sa.Float(), nullable=True),
        sa.Column('turn', sa.Float(), nullable=True),
        sa.Column('suspended', sa.Boolean(), nullable=False),
        sa.Column('is_st', sa.Boolean(), nullable=False),
        sa.Column('pe_ttm', sa.Float(), nullable=True),
        sa.Column('pb_mrq', sa.Float(), nullable=True),
        sa.Column('ps_ttm', sa.Float(), nullable=True),
        sa.Column('pcf_ttm', sa.Float(), nullable=True),
        sa.Column('total_mv', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('code', 'trade_date'),
    )
    op.create_index('ix_daily_basic_trade_date', 'daily_basic', ['trade_date'])


def downgrade() -> None:
    op.drop_index('ix_daily_basic_trade_date', table_name='daily_basic')
    op.drop_table('daily_basic')
