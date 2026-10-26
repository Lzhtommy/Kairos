"""users.username 作为登录名，email 改为可空

Revision ID: f6f7f39f72d8
Revises: ab249f59d8c3
Create Date: 2026-10-26 10:00:00
"""
import re
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f6f7f39f72d8'
down_revision: Union[str, None] = 'ab249f59d8c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _base_username(email: str, user_id: int) -> str:
    """邮箱 @ 前缀 → 合法用户名（小写、仅 [a-z0-9_]、3-20 位）。"""
    name = re.sub(r"[^a-z0-9_]", "_", email.split("@")[0].lower()).strip("_")[:20]
    return name if len(name) >= 3 else f"user{user_id}"


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('username', sa.String(length=32), nullable=True))

    # 老用户回填：取邮箱前缀，重名追加数字。登录接口仍接受邮箱，老用户无感知。
    conn = op.get_bind()
    taken: set[str] = set()
    for user_id, email in conn.execute(sa.text("SELECT id, email FROM users ORDER BY id")):
        base = _base_username(email or "", user_id)
        name, n = base, 2
        while name in taken:
            suffix = str(n)
            name = base[: 20 - len(suffix)] + suffix
            n += 1
        taken.add(name)
        conn.execute(
            sa.text("UPDATE users SET username = :u WHERE id = :id"), {"u": name, "id": user_id}
        )

    # SQLite 下 batch 会重建 users 表；alembic 的连接没开 foreign_keys，
    # 所以 DROP 旧表不会级联删掉策略 / 模拟盘等子表数据。
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('username', existing_type=sa.String(length=32), nullable=False)
        batch_op.alter_column('email', existing_type=sa.String(length=255), nullable=True)
        batch_op.create_index(batch_op.f('ix_users_username'), ['username'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_username'))
        batch_op.alter_column('email', existing_type=sa.String(length=255), nullable=False)
        batch_op.drop_column('username')
