"""主要指数日线 index_bar；指数成分加权重列

Revision ID: a6c9e2f48d13
Revises: f1b3e6a27c08
Create Date: 2026-11-09 15:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a6c9e2f48d13'
down_revision: Union[str, None] = 'f1b3e6a27c08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('index_constituent', sa.Column('weight', sa.Float(), nullable=True))
    op.create_table(
        'index_bar',
        sa.Column('index_code', sa.String(16), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('open', sa.Float(), nullable=False),
        sa.Column('high', sa.Float(), nullable=False),
        sa.Column('low', sa.Float(), nullable=False),
        sa.Column('close', sa.Float(), nullable=False),
        sa.Column('volume', sa.Float(), nullable=True),
        sa.Column('amount', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('index_code', 'trade_date'),
    )


def downgrade() -> None:
    op.drop_table('index_bar')
    with op.batch_alter_table('index_constituent', schema=None) as batch_op:
        batch_op.drop_column('weight')
