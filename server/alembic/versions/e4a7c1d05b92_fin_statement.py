"""三大报表主要科目 fin_statement

Revision ID: e4a7c1d05b92
Revises: d2f5b8c93a41
Create Date: 2026-11-09 13:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e4a7c1d05b92'
down_revision: Union[str, None] = 'd2f5b8c93a41'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 与 kairos_data.schema.fin_statement 一致；Float 在 MySQL 下编译为 DOUBLE
    op.create_table(
        'fin_statement',
        sa.Column('code', sa.String(16), nullable=False),
        sa.Column('report_date', sa.Date(), nullable=False),
        sa.Column('notice_date', sa.Date(), nullable=True),
        sa.Column('total_assets', sa.Float(), nullable=True),  # 总资产
        sa.Column('monetary_funds', sa.Float(), nullable=True),  # 货币资金
        sa.Column('accounts_rece', sa.Float(), nullable=True),  # 应收账款
        sa.Column('inventory', sa.Float(), nullable=True),  # 存货
        sa.Column('fixed_asset', sa.Float(), nullable=True),  # 固定资产
        sa.Column('loan_advance', sa.Float(), nullable=True),  # 发放贷款及垫款
        sa.Column('total_liabilities', sa.Float(), nullable=True),  # 总负债
        sa.Column('short_loan', sa.Float(), nullable=True),  # 短期借款
        sa.Column('accounts_payable', sa.Float(), nullable=True),  # 应付账款
        sa.Column('advance_receivables', sa.Float(), nullable=True),  # 预收款项
        sa.Column('accept_deposit', sa.Float(), nullable=True),  # 吸收存款
        sa.Column('total_equity', sa.Float(), nullable=True),  # 股东权益
        sa.Column('current_ratio', sa.Float(), nullable=True),  # 流动比率 %
        sa.Column('debt_asset_ratio', sa.Float(), nullable=True),  # 资产负债率 %
        sa.Column('operate_income', sa.Float(), nullable=True),  # 营业总收入
        sa.Column('operate_total_cost', sa.Float(), nullable=True),  # 营业总成本
        sa.Column('operate_cost', sa.Float(), nullable=True),  # 营业成本
        sa.Column('sale_expense', sa.Float(), nullable=True),  # 销售费用
        sa.Column('manage_expense', sa.Float(), nullable=True),  # 管理费用
        sa.Column('finance_expense', sa.Float(), nullable=True),  # 财务费用
        sa.Column('operate_tax', sa.Float(), nullable=True),  # 税金及附加
        sa.Column('interest_ni', sa.Float(), nullable=True),  # 利息净收入
        sa.Column('fee_commission_ni', sa.Float(), nullable=True),  # 手续费及佣金净收入
        sa.Column('invest_income', sa.Float(), nullable=True),  # 投资收益
        sa.Column('operate_profit', sa.Float(), nullable=True),  # 营业利润
        sa.Column('total_profit', sa.Float(), nullable=True),  # 利润总额
        sa.Column('income_tax', sa.Float(), nullable=True),  # 所得税
        sa.Column('parent_netprofit', sa.Float(), nullable=True),  # 归母净利润
        sa.Column('deduct_netprofit', sa.Float(), nullable=True),  # 扣非归母净利润
        sa.Column('ocf', sa.Float(), nullable=True),  # 经营活动现金流净额
        sa.Column('sales_cash', sa.Float(), nullable=True),  # 销售商品收到的现金
        sa.Column('staff_cash', sa.Float(), nullable=True),  # 支付给职工的现金
        sa.Column('icf', sa.Float(), nullable=True),  # 投资活动现金流净额
        sa.Column('capex', sa.Float(), nullable=True),  # 购建长期资产支付的现金
        sa.Column('fcf', sa.Float(), nullable=True),  # 筹资活动现金流净额
        sa.Column('cash_change', sa.Float(), nullable=True),  # 现金及等价物净增加额
        sa.PrimaryKeyConstraint('code', 'report_date'),
    )
    op.create_index('ix_fin_statement_report_date', 'fin_statement', ['report_date'])


def downgrade() -> None:
    op.drop_table('fin_statement')
