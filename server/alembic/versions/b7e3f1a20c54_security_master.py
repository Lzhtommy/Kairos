"""security_master：证券主表（含已退市证券与上市 / 退市日期）

Revision ID: b7e3f1a20c54
Revises: 9e4b2c6d8a13
Create Date: 2026-11-06 10:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7e3f1a20c54'
down_revision: Union[str, None] = '9e4b2c6d8a13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'security_master',
        sa.Column('exchange', sa.String(length=8), nullable=False),
        sa.Column('code', sa.String(length=16), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('sec_type', sa.String(length=8), nullable=False),
        sa.Column('list_date', sa.Date(), nullable=True),
        sa.Column('delist_date', sa.Date(), nullable=True),
        sa.Column('status', sa.String(length=8), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        # DateTime 在 MySQL 下由 app.core.db 编译为 DATETIME(6)
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('exchange', 'code'),
    )
    op.create_index('ix_security_master_sec_type', 'security_master', ['sec_type'])


def downgrade() -> None:
    op.drop_index('ix_security_master_sec_type', table_name='security_master')
    op.drop_table('security_master')
