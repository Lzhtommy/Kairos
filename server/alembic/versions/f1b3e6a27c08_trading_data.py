"""交易类数据：融资融券、龙虎榜、大宗交易

Revision ID: f1b3e6a27c08
Revises: e4a7c1d05b92
Create Date: 2026-11-09 14:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1b3e6a27c08'
down_revision: Union[str, None] = 'e4a7c1d05b92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 与 kairos_data.schema 一致；Float 在 MySQL 下编译为 DOUBLE
    op.create_table(
        'margin_trading',
        sa.Column('code', sa.String(16), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('rzye', sa.Float(), nullable=True),
        sa.Column('rzmre', sa.Float(), nullable=True),
        sa.Column('rzche', sa.Float(), nullable=True),
        sa.Column('rqye', sa.Float(), nullable=True),
        sa.Column('rqyl', sa.Float(), nullable=True),
        sa.Column('rqmcl', sa.Float(), nullable=True),
        sa.Column('rzrqye', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('code', 'trade_date'),
    )
    op.create_index('ix_margin_trading_trade_date', 'margin_trading', ['trade_date'])
    op.create_table(
        'billboard',
        sa.Column('code', sa.String(16), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('reason', sa.String(128), nullable=False),
        sa.Column('close', sa.Float(), nullable=True),
        sa.Column('change_pct', sa.Float(), nullable=True),
        sa.Column('turnover_rate', sa.Float(), nullable=True),
        sa.Column('buy_amt', sa.Float(), nullable=True),
        sa.Column('sell_amt', sa.Float(), nullable=True),
        sa.Column('net_amt', sa.Float(), nullable=True),
        sa.Column('deal_amt', sa.Float(), nullable=True),
        sa.Column('deal_ratio', sa.Float(), nullable=True),
        sa.Column('comment', sa.String(256), nullable=False),
        sa.PrimaryKeyConstraint('code', 'trade_date', 'reason'),
    )
    op.create_index('ix_billboard_trade_date', 'billboard', ['trade_date'])
    op.create_table(
        'block_trade',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(16), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('price', sa.Float(), nullable=True),
        sa.Column('premium_pct', sa.Float(), nullable=True),
        sa.Column('volume', sa.BigInteger(), nullable=True),
        sa.Column('amount', sa.Float(), nullable=True),
        sa.Column('buyer', sa.String(128), nullable=False),
        sa.Column('seller', sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_block_trade_code', 'block_trade', ['code'])
    op.create_index('ix_block_trade_trade_date', 'block_trade', ['trade_date'])


def downgrade() -> None:
    for t in ('block_trade', 'billboard', 'margin_trading'):
        op.drop_table(t)
