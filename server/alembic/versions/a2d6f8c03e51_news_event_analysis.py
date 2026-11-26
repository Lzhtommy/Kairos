"""要闻二次分析：news_event.themes / ai_stocks / reason / impact / analyzed_at

Revision ID: a2d6f8c03e51
Revises: c5a9e1f37b20
Create Date: 2026-11-26 15:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a2d6f8c03e51'
down_revision: Union[str, None] = 'c5a9e1f37b20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLS = [
    sa.Column('themes', sa.JSON(), nullable=True),
    sa.Column('ai_stocks', sa.JSON(), nullable=True),
    sa.Column('reason', sa.String(128), nullable=True),
    sa.Column('impact', sa.JSON(), nullable=True),
    sa.Column('analyzed_at', sa.DateTime(timezone=True), nullable=True),
]


def upgrade() -> None:
    cols = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('news_event')}
    for c in _COLS:
        if c.name not in cols:
            op.add_column('news_event', c)


def downgrade() -> None:
    for c in reversed(_COLS):
        op.drop_column('news_event', c.name)
