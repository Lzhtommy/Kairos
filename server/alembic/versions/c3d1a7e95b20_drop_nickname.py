"""去掉昵称：合法的昵称转为用户名，然后删除 users.nickname

Revision ID: c3d1a7e95b20
Revises: f6f7f39f72d8
Create Date: 2026-10-26 12:00:00
"""
import re
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d1a7e95b20'
down_revision: Union[str, None] = 'f6f7f39f72d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_USERNAME_RE = re.compile(r"^[a-z0-9_]{3,20}$")


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, username, nickname FROM users ORDER BY id")).all()

    # 第一轮：昵称（转小写）合法且不重复的，直接作为用户名
    final: dict[int, str] = {}
    taken: set[str] = set()
    for user_id, _username, nickname in rows:
        wanted = (nickname or "").strip().lower()
        if _USERNAME_RE.fullmatch(wanted) and wanted not in taken:
            final[user_id] = wanted
            taken.add(wanted)
    # 第二轮：其余（如中文昵称）保留现用户名，若被别人的新名占了就追加数字
    for user_id, username, _nickname in rows:
        if user_id in final:
            continue
        name, n = username, 2
        while name in taken:
            suffix = str(n)
            name = username[: 20 - len(suffix)] + suffix
            n += 1
        final[user_id] = name
        taken.add(name)

    # 先改成临时名再改成最终名，避免互换用户名时撞唯一索引
    for user_id in final:
        conn.execute(
            sa.text("UPDATE users SET username = :u WHERE id = :id"),
            {"u": f"__tmp_{user_id}", "id": user_id},
        )
    for user_id, name in final.items():
        conn.execute(
            sa.text("UPDATE users SET username = :u WHERE id = :id"), {"u": name, "id": user_id}
        )

    # SQLite 下 batch 会重建 users 表；alembic 的连接没开 foreign_keys，不会级联删子表
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('nickname')


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('nickname', sa.String(length=64), nullable=True))
    op.execute("UPDATE users SET nickname = username")
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('nickname', existing_type=sa.String(length=64), nullable=False)
