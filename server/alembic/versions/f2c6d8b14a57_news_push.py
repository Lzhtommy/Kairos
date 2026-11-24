"""快讯要闻推送：user_settings.news_push_level / news_webhook，news_event.pushed_at

Revision ID: f2c6d8b14a57
Revises: e5b1c7a93d40
Create Date: 2026-11-24 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f2c6d8b14a57'
down_revision: Union[str, None] = 'e5b1c7a93d40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    tables = insp.get_table_names()
    # user_settings 由应用启动时 create_all 建，全新库跑迁移时还不存在（届时 create_all 会带上新列）
    if 'user_settings' in tables:
        cols = {c['name'] for c in insp.get_columns('user_settings')}
        if 'news_push_level' not in cols:
            op.add_column('user_settings', sa.Column('news_push_level', sa.String(16), nullable=False,
                                                     server_default='off'))
        if 'news_webhook' not in cols:
            op.add_column('user_settings', sa.Column('news_webhook', sa.String(512), nullable=False,
                                                     server_default=''))
    if 'pushed_at' not in {c['name'] for c in insp.get_columns('news_event')}:
        op.add_column('news_event', sa.Column('pushed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('news_event', 'pushed_at')
    op.drop_column('user_settings', 'news_webhook')
    op.drop_column('user_settings', 'news_push_level')
