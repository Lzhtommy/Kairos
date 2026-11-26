"""daily_basic 并入 factor_history：加 industry 列，搬入北交所行与当天行业

factor_history（收盘因子快照，2025-06-24 起）与 daily_basic 的 PE / PB / 市值 / 换手 / 价格重复，
独有的只有三样：北交所（BaoStock 没有）、当天行业、ROE / 股息率。前两样搬进 daily_basic，
ROE / 股息率改为读时由 fin_report / dividend 现算（app.services.pit），不落库。
factor_history 本迁移不删，核对无误后由下一个迁移删除。

MySQL（daily_basic 约 1900 万行）：
- ADD COLUMN 用 ALGORITHM=INSTANT，只改元数据不重建表；
- 行业按交易日分批 UPDATE（每批约 5500 行、各自提交），不开一个上百万行的大事务；
- 北交所约 12 万行一条 INSERT … SELECT，全在服务器端完成。
每步只处理还没做的部分（industry IS NULL / NOT EXISTS），中途失败可直接重跑。

北交所行的近似：preclose 由收盘价与涨跌幅倒推，成交量取当日日 K，成交额为 0 视为停牌，
ST 状态按当前名称；ps_ttm / pcf_ttm 为空。

Revision ID: b3f7d2a85e19
Revises: f2c6d8b14a57
Create Date: 2026-11-26 16:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3f7d2a85e19'
down_revision: Union[str, None] = 'f2c6d8b14a57'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BJ_INSERT = """
INSERT INTO daily_basic (code, trade_date, close, preclose, volume, amount, turn, suspended, is_st,
                         pe_ttm, pb_mrq, total_mv, industry)
SELECT f.code, f.ts, f.price,
       CASE WHEN f.change_pct > -100 THEN ROUND(f.price / (1 + f.change_pct / 100), 2) END,
       k.volume, f.turnover * 100000000, f.turnover_rate, f.turnover <= 0, s.name LIKE '%ST%',
       f.pe, f.pb, CASE WHEN f.market_cap > 0 THEN f.market_cap * 100000000 END, NULLIF(f.industry, '—')
FROM factor_history f
JOIN stock_info s ON s.code = f.code AND s.market = 'BJ'
LEFT JOIN kline k ON k.code = f.code AND k.period = '1d' AND k.ts = f.ts
WHERE f.price > 0
  AND NOT EXISTS (SELECT 1 FROM daily_basic d WHERE d.code = f.code AND d.trade_date = f.ts)
"""


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    mysql = bind.dialect.name == "mysql"
    if "industry" not in {c["name"] for c in insp.get_columns("daily_basic")}:
        if mysql:
            op.execute("ALTER TABLE daily_basic ADD COLUMN industry VARCHAR(64) NULL, ALGORITHM=INSTANT")
        else:
            op.add_column("daily_basic", sa.Column("industry", sa.String(64), nullable=True))
    if not insp.has_table("factor_history"):  # 全新库：没有快照可搬
        return

    if not mysql:  # SQLite（本地开发）：迁移在事务里，整体原子
        op.execute(
            "UPDATE daily_basic SET industry = (SELECT f.industry FROM factor_history f "
            "WHERE f.code = daily_basic.code AND f.ts = daily_basic.trade_date AND f.industry <> '—') "
            "WHERE industry IS NULL"
        )
        op.execute(_BJ_INSERT)
        return

    with op.get_context().autocommit_block():
        days = [r[0] for r in bind.execute(sa.text("SELECT DISTINCT ts FROM factor_history ORDER BY ts"))]
        for day in days:
            bind.execute(sa.text(
                "UPDATE daily_basic d JOIN factor_history f ON f.code = d.code AND f.ts = d.trade_date "
                "SET d.industry = f.industry "
                "WHERE d.trade_date = :day AND d.industry IS NULL AND f.ts = :day AND f.industry <> '—'"
            ), {"day": day})
        bind.execute(sa.text(_BJ_INSERT))


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DELETE FROM daily_basic WHERE code IN (SELECT code FROM stock_info WHERE market = 'BJ')")
    if bind.dialect.name == "mysql":
        op.execute("ALTER TABLE daily_basic DROP COLUMN industry, ALGORITHM=INSTANT")
    else:
        op.drop_column("daily_basic", "industry")
