"""stock_info 加上市 / 退市日期（退市股也收进股票池供查阅）

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
    op.add_column('stock_info', sa.Column('list_date', sa.Date(), nullable=True))
    op.add_column('stock_info', sa.Column('delist_date', sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('stock_info', schema=None) as batch_op:
        batch_op.drop_column('delist_date')
        batch_op.drop_column('list_date')
