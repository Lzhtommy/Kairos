"""删除 factor_history，去掉 daily_basic.industry

b3f7d2a85e19 搬完后核对：行业 154 万行与快照逐行一致，北交所行全部到位。但按天存行业是冗余的——
2025-07 ~ 2026-11 约 5900 只股票里只有 4 只改过行业，统一改用 stock_info 当前行业。
现算的 ROE / 股息率与快照值抽样一致（茅台等白酒股差 0.0x ~ 2 个百分点，来自 TTM 滚算口径）。

MySQL 8.0.29+ 的 DROP COLUMN 可 INSTANT，不重建 daily_basic。
downgrade 只加回空的 industry 列；factor_history 不重建（数据已删）。

Revision ID: c5a9e1f37b20
Revises: b3f7d2a85e19
Create Date: 2026-11-26 12:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c5a9e1f37b20'
down_revision: Union[str, None] = 'b3f7d2a85e19'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "industry" in {c["name"] for c in insp.get_columns("daily_basic")}:
        if bind.dialect.name == "mysql":
            op.execute("ALTER TABLE daily_basic DROP COLUMN industry, ALGORITHM=INSTANT")
        else:
            op.drop_column("daily_basic", "industry")
    if insp.has_table("factor_history"):
        op.drop_table("factor_history")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        op.execute("ALTER TABLE daily_basic ADD COLUMN industry VARCHAR(64) NULL, ALGORITHM=INSTANT")
    else:
        op.add_column("daily_basic", sa.Column("industry", sa.String(64), nullable=True))
