"""快讯事件串联：news_event.parent_id / thread_sources

Revision ID: e5b1c7a93d40
Revises: d3a8f5c19e24
Create Date: 2026-11-23 10:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5b1c7a93d40'
down_revision: Union[str, None] = 'd3a8f5c19e24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    cols = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('news_event')}
    if 'parent_id' not in cols:
        op.add_column('news_event', sa.Column('parent_id', sa.Integer(), nullable=True))
        op.create_index('ix_news_event_parent_id', 'news_event', ['parent_id'])
    if 'thread_sources' not in cols:
        op.add_column('news_event', sa.Column('thread_sources', sa.SmallInteger(), nullable=True))


def downgrade() -> None:
    op.drop_index('ix_news_event_parent_id', 'news_event')
    op.drop_column('news_event', 'thread_sources')
    op.drop_column('news_event', 'parent_id')
