"""股本变动、股东户数、十大股东、限售解禁

Revision ID: d2f5b8c93a41
Revises: c8a4d2e61f37
Create Date: 2026-11-09 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd2f5b8c93a41'
down_revision: Union[str, None] = 'c8a4d2e61f37'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 表结构与 kairos_data.schema 一致；Float 在 MySQL 下由 kairos_data.store 编译为 DOUBLE
    op.create_table(
        'share_capital',
        sa.Column('code', sa.String(16), nullable=False),
        sa.Column('change_date', sa.Date(), nullable=False),
        sa.Column('notice_date', sa.Date(), nullable=True),
        sa.Column('total_shares', sa.BigInteger(), nullable=True),
        sa.Column('float_a_shares', sa.BigInteger(), nullable=True),
        sa.Column('limited_shares', sa.BigInteger(), nullable=True),
        sa.Column('change_reason', sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint('code', 'change_date'),
    )
    op.create_table(
        'holder_count',
        sa.Column('code', sa.String(16), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=False),
        sa.Column('notice_date', sa.Date(), nullable=True),
        sa.Column('holder_num', sa.BigInteger(), nullable=True),
        sa.Column('avg_hold_shares', sa.Float(), nullable=True),
        sa.Column('total_a_shares', sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint('code', 'end_date'),
    )
    op.create_index('ix_holder_count_end_date', 'holder_count', ['end_date'])
    op.create_table(
        'top_holder',
        sa.Column('code', sa.String(16), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=False),
        sa.Column('kind', sa.String(8), nullable=False),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('holder_name', sa.String(256), nullable=False),
        sa.Column('holder_type', sa.String(32), nullable=False),
        sa.Column('shares_type', sa.String(64), nullable=False),
        sa.Column('hold_num', sa.BigInteger(), nullable=True),
        sa.Column('hold_ratio', sa.Float(), nullable=True),
        sa.Column('change', sa.String(32), nullable=False),
        sa.Column('notice_date', sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint('code', 'end_date', 'kind', 'rank'),
    )
    op.create_index('ix_top_holder_end_date', 'top_holder', ['end_date'])
    op.create_index('ix_top_holder_holder_name', 'top_holder', ['holder_name'])
    op.create_table(
        'lockup',
        sa.Column('code', sa.String(16), nullable=False),
        sa.Column('free_date', sa.Date(), nullable=False),
        sa.Column('shares_type', sa.String(64), nullable=False),
        sa.Column('holder_num', sa.Integer(), nullable=True),
        sa.Column('free_shares', sa.Float(), nullable=True),
        sa.Column('able_free_shares', sa.Float(), nullable=True),
        sa.Column('market_cap', sa.Float(), nullable=True),
        sa.Column('float_ratio', sa.Float(), nullable=True),
        sa.Column('total_ratio', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('code', 'free_date', 'shares_type'),
    )
    op.create_index('ix_lockup_free_date', 'lockup', ['free_date'])


def downgrade() -> None:
    for t in ('lockup', 'top_holder', 'holder_count', 'share_capital'):
        op.drop_table(t)
