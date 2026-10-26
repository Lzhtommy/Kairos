"""users.token_version：改密 / 重置密码后让旧 token 失效

Revision ID: 9e4b2c6d8a13
Revises: c3d1a7e95b20
Create Date: 2026-10-26 14:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9e4b2c6d8a13'
down_revision: Union[str, None] = 'c3d1a7e95b20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite 原生支持带默认值的 ADD COLUMN，不重建表
    op.add_column(
        'users', sa.Column('token_version', sa.Integer(), nullable=False, server_default='0')
    )


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('token_version')
