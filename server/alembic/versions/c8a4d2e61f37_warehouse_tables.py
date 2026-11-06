"""数据仓库表：财报、分红、指数成分、宏观利率、公告

Revision ID: c8a4d2e61f37
Revises: b7e3f1a20c54
Create Date: 2026-11-06 19:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8a4d2e61f37'
down_revision: Union[str, None] = 'b7e3f1a20c54'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Float / DateTime 在 MySQL 下由 app.core.db 编译为 DOUBLE / DATETIME(6)
    op.create_table(
        'fin_report',
        sa.Column('code', sa.String(16), nullable=False),
        sa.Column('report_date', sa.Date(), nullable=False),
        sa.Column('notice_date', sa.Date(), nullable=True),
        sa.Column('eps', sa.Float(), nullable=True),
        sa.Column('deduct_eps', sa.Float(), nullable=True),
        sa.Column('revenue', sa.Float(), nullable=True),
        sa.Column('net_profit', sa.Float(), nullable=True),
        sa.Column('revenue_yoy', sa.Float(), nullable=True),
        sa.Column('profit_yoy', sa.Float(), nullable=True),
        sa.Column('roe', sa.Float(), nullable=True),
        sa.Column('bps', sa.Float(), nullable=True),
        sa.Column('ocf_ps', sa.Float(), nullable=True),
        sa.Column('gross_margin', sa.Float(), nullable=True),
        sa.Column('dividend_plan', sa.String(128), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('code', 'report_date'),
    )
    op.create_index('ix_fin_report_report_date', 'fin_report', ['report_date'])
    op.create_index('ix_fin_report_notice_date', 'fin_report', ['notice_date'])

    op.create_table(
        'dividend',
        sa.Column('code', sa.String(16), nullable=False),
        sa.Column('report_date', sa.Date(), nullable=False),
        sa.Column('plan_notice_date', sa.Date(), nullable=True),
        sa.Column('notice_date', sa.Date(), nullable=True),
        sa.Column('record_date', sa.Date(), nullable=True),
        sa.Column('ex_date', sa.Date(), nullable=True),
        sa.Column('cash_per10', sa.Float(), nullable=True),
        sa.Column('bonus_per10', sa.Float(), nullable=True),
        sa.Column('transfer_per10', sa.Float(), nullable=True),
        sa.Column('progress', sa.String(32), nullable=False),
        sa.Column('plan', sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint('code', 'report_date'),
    )
    op.create_index('ix_dividend_report_date', 'dividend', ['report_date'])
    op.create_index('ix_dividend_ex_date', 'dividend', ['ex_date'])

    op.create_table(
        'index_constituent',
        sa.Column('index_code', sa.String(16), nullable=False),
        sa.Column('snapshot_date', sa.Date(), nullable=False),
        sa.Column('code', sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint('index_code', 'snapshot_date', 'code'),
    )
    op.create_index('ix_index_constituent_code', 'index_constituent', ['code'])

    op.create_table(
        'macro_series',
        sa.Column('indicator', sa.String(32), nullable=False),
        sa.Column('period', sa.Date(), nullable=False),
        sa.Column('value', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('indicator', 'period'),
    )

    op.create_table(
        'announcement',
        sa.Column('art_code', sa.String(32), nullable=False),
        sa.Column('code', sa.String(16), nullable=False),
        sa.Column('title', sa.String(512), nullable=False),
        sa.Column('category', sa.String(64), nullable=False),
        sa.Column('ann_date', sa.Date(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('summary_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('art_code'),
    )
    op.create_index('ix_announcement_code', 'announcement', ['code'])
    op.create_index('ix_announcement_ann_date', 'announcement', ['ann_date'])


def downgrade() -> None:
    for t in ('announcement', 'macro_series', 'index_constituent', 'dividend', 'fin_report'):
        op.drop_table(t)
