"""sync_state：全量回补完成标记

Revision ID: c3e5a7b92f16
Revises: b8d2f4a61e75
Create Date: 2026-11-09 17:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3e5a7b92f16'
down_revision: Union[str, None] = 'b8d2f4a61e75'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sync_state',
        sa.Column('key', sa.String(64), nullable=False),
        sa.Column('value', sa.String(256), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )


def downgrade() -> None:
    op.drop_table('sync_state')
