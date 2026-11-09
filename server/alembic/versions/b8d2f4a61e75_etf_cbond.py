"""ETF 与可转债：security_info 清单、security_bar 日线

Revision ID: b8d2f4a61e75
Revises: a6c9e2f48d13
Create Date: 2026-11-09 16:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8d2f4a61e75'
down_revision: Union[str, None] = 'a6c9e2f48d13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'security_info',
        sa.Column('code', sa.String(16), nullable=False),
        sa.Column('exchange', sa.String(8), nullable=False),
        sa.Column('sec_type', sa.String(8), nullable=False),
        sa.Column('name', sa.String(64), nullable=False),
        sa.Column('list_date', sa.Date(), nullable=True),
        sa.Column('delist_date', sa.Date(), nullable=True),
        sa.Column('stock_code', sa.String(16), nullable=True),
        sa.Column('rating', sa.String(16), nullable=True),
        sa.Column('issue_size', sa.Float(), nullable=True),
        sa.Column('maturity_date', sa.Date(), nullable=True),
        sa.Column('convert_price', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('code'),
    )
    op.create_index('ix_security_info_sec_type', 'security_info', ['sec_type'])
    op.create_index('ix_security_info_stock_code', 'security_info', ['stock_code'])
    op.create_table(
        'security_bar',
        sa.Column('code', sa.String(16), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('open', sa.Float(), nullable=False),
        sa.Column('high', sa.Float(), nullable=False),
        sa.Column('low', sa.Float(), nullable=False),
        sa.Column('close', sa.Float(), nullable=False),
        sa.Column('volume', sa.Float(), nullable=True),
        sa.Column('amount', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('code', 'trade_date'),
    )


def downgrade() -> None:
    op.drop_table('security_bar')
    op.drop_table('security_info')
