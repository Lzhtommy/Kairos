"""全球快讯：news_item

Revision ID: c7d2e9a41f63
Revises: b4e8d1f7a920
Create Date: 2026-11-20 16:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c7d2e9a41f63'
down_revision: Union[str, None] = 'b4e8d1f7a920'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 与 app/models/news.py 一致
    if 'news_item' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'news_item',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('source', sa.String(8), nullable=False),
        sa.Column('source_id', sa.String(40), nullable=False),
        sa.Column('pub_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('title', sa.String(256), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('url', sa.String(512), nullable=True),
        sa.Column('importance', sa.SmallInteger(), nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=True),
        sa.UniqueConstraint('source', 'source_id', name='uq_news_source_item'),
    )
    op.create_index('ix_news_item_pub_at', 'news_item', ['pub_at'])
    op.create_index('ix_news_item_seen_at', 'news_item', ['seen_at'])
    op.create_index('ix_news_item_event_id', 'news_item', ['event_id'])


def downgrade() -> None:
    op.drop_table('news_item')
