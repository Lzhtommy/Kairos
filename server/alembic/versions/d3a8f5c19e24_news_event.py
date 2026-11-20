"""快讯事件 LLM 标注：news_event；news_item 加 stocks

Revision ID: d3a8f5c19e24
Revises: c7d2e9a41f63
Create Date: 2026-11-20 18:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd3a8f5c19e24'
down_revision: Union[str, None] = 'c7d2e9a41f63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 与 app/models/news.py 一致
    insp = sa.inspect(op.get_bind())
    if 'stocks' not in {c['name'] for c in insp.get_columns('news_item')}:
        op.add_column('news_item', sa.Column('stocks', sa.JSON(), nullable=True))
    if 'news_event' not in insp.get_table_names():
        op.create_table(
            'news_event',
            sa.Column('event_id', sa.Integer(), primary_key=True, autoincrement=False),
            sa.Column('category', sa.String(16), nullable=True),
            sa.Column('score', sa.SmallInteger(), nullable=True),
            sa.Column('sectors', sa.JSON(), nullable=False),
            sa.Column('model', sa.String(32), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index('ix_news_event_category', 'news_event', ['category'])


def downgrade() -> None:
    op.drop_table('news_event')
    op.drop_column('news_item', 'stocks')
