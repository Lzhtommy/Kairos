"""AI 操盘手：agent_accounts / positions / orders / equity / sessions / memories / playbooks

Revision ID: b4e8d1f7a920
Revises: a9e4c7f20b58
Create Date: 2026-11-12 10:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b4e8d1f7a920'
down_revision: Union[str, None] = 'a9e4c7f20b58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _fk() -> sa.ForeignKey:
    return sa.ForeignKey('agent_accounts.id', ondelete='CASCADE')


def upgrade() -> None:
    # 与 app/models/agent.py 一致；交易日字段是 DATE（ORM 侧 TradeDay）
    op.create_table(
        'agent_accounts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(64), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('initial_cash', sa.Float(), nullable=False),
        sa.Column('cash', sa.Float(), nullable=False),
        sa.Column('fees', sa.JSON(), nullable=False),
        sa.Column('benchmark', sa.String(16), nullable=False),
        sa.Column('playbook', sa.Text(), nullable=False),
        sa.Column('playbook_version', sa.Integer(), nullable=False),
        sa.Column('last_settled', sa.Date(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        'agent_positions',
        sa.Column('account_id', sa.Integer(), _fk(), primary_key=True),
        sa.Column('code', sa.String(16), primary_key=True),
        sa.Column('name', sa.String(64), nullable=False),
        sa.Column('shares', sa.Integer(), nullable=False),
        sa.Column('cost', sa.Float(), nullable=False),
        sa.Column('last_price', sa.Float(), nullable=False),
        sa.Column('opened_at', sa.Date(), nullable=False),
    )
    op.create_table(
        'agent_orders',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.Integer(), _fk(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=True),
        sa.Column('code', sa.String(16), nullable=False),
        sa.Column('name', sa.String(64), nullable=False),
        sa.Column('side', sa.String(4), nullable=False),
        sa.Column('shares', sa.Integer(), nullable=False),
        sa.Column('limit_price', sa.Float(), nullable=True),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('memory_ids', sa.JSON(), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=True),
        sa.Column('filled_shares', sa.Integer(), nullable=False),
        sa.Column('fill_price', sa.Float(), nullable=True),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('fee', sa.Float(), nullable=False),
        sa.Column('realized_pnl', sa.Float(), nullable=True),
        sa.Column('note', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_agent_orders_account_id', 'agent_orders', ['account_id'])
    op.create_index('ix_agent_orders_status', 'agent_orders', ['status'])
    op.create_table(
        'agent_equity',
        sa.Column('account_id', sa.Integer(), _fk(), primary_key=True),
        sa.Column('date', sa.Date(), primary_key=True),
        sa.Column('cash', sa.Float(), nullable=False),
        sa.Column('market_value', sa.Float(), nullable=False),
        sa.Column('total', sa.Float(), nullable=False),
        sa.Column('daily_return', sa.Float(), nullable=False),
        sa.Column('bench_close', sa.Float(), nullable=True),
        sa.Column('fees', sa.Float(), nullable=False),
    )
    op.create_table(
        'agent_sessions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.Integer(), _fk(), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('steps', sa.JSON(), nullable=False),
        sa.Column('tokens', sa.Integer(), nullable=False),
        sa.Column('model', sa.String(64), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_agent_sessions_account_id', 'agent_sessions', ['account_id'])
    op.create_index('ix_agent_sessions_trade_date', 'agent_sessions', ['trade_date'])
    op.create_table(
        'agent_memories',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.Integer(), _fk(), nullable=False),
        sa.Column('kind', sa.String(12), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('tags', sa.JSON(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('retire_reason', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('retired_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_agent_memories_account_id', 'agent_memories', ['account_id'])
    op.create_table(
        'agent_playbooks',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.Integer(), _fk(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('rationale', sa.Text(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_agent_playbooks_account_id', 'agent_playbooks', ['account_id'])


def downgrade() -> None:
    for t in ('agent_playbooks', 'agent_memories', 'agent_sessions', 'agent_equity', 'agent_orders',
              'agent_positions', 'agent_accounts'):
        op.drop_table(t)
