"""数据库表结构（SQLAlchemy Core）：股票池、日 K、财报与三大报表、分红、股本股东、交易类数据、每日指标（ST / 停牌 / 换手 / 估值）、指数、ETF 与可转债、宏观、公告。

单独使用时 `metadata.create_all(engine)` 建表；嵌入 Kairos 时由 app.models 把这些表
复制进业务库的 metadata 并映射 ORM 类，两边共用同一份定义。
Float / DateTime 在 MySQL 下的编译口径见 kairos_data.store。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (BigInteger, Boolean, Column, Date, DateTime, Float, Integer, MetaData, String,
                        Table, Text)

metadata = MetaData()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# 股票池：在市 + 已退市 A 股。上市 / 退市日期来自 BaoStock（北交所没有，留空）
stock_info = Table(
    "stock_info", metadata,
    Column("code", String(16), primary_key=True),
    Column("name", String(64), nullable=False),
    Column("market", String(8), nullable=False),  # SH / SZ / BJ
    Column("industry", String(64), nullable=False),
    Column("list_date", Date, nullable=True),
    Column("delist_date", Date, nullable=True),
)

# 日 K（前复权，成交量单位：手）。主键 (code, period, ts) 让 InnoDB 按股连续存放，
# 按股取最近 N 根是主键范围扫描；按日取全市场走 ts 索引
kline = Table(
    "kline", metadata,
    Column("code", String(16), primary_key=True),
    Column("period", String(8), primary_key=True, default="1d"),
    Column("ts", DateTime(timezone=True), primary_key=True, index=True),
    Column("open", Float, nullable=False),
    Column("high", Float, nullable=False),
    Column("low", Float, nullable=False),
    Column("close", Float, nullable=False),
    Column("volume", BigInteger, nullable=False),
)

# 定期报告主要指标（东财业绩报表），每股每报告期一行，保留最新披露版本
fin_report = Table(
    "fin_report", metadata,
    Column("code", String(16), primary_key=True),
    Column("report_date", Date, primary_key=True, index=True),  # 报告期末
    Column("notice_date", Date, nullable=True, index=True),  # 公告日
    Column("eps", Float, nullable=True),
    Column("deduct_eps", Float, nullable=True),  # 扣非每股收益
    Column("revenue", Float, nullable=True),  # 营业总收入（元）
    Column("net_profit", Float, nullable=True),  # 归母净利润（元）
    Column("revenue_yoy", Float, nullable=True),  # %
    Column("profit_yoy", Float, nullable=True),  # %
    Column("roe", Float, nullable=True),  # 加权 ROE %
    Column("bps", Float, nullable=True),  # 每股净资产
    Column("ocf_ps", Float, nullable=True),  # 每股经营现金流
    Column("gross_margin", Float, nullable=True),  # 销售毛利率 %
    Column("dividend_plan", String(128), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False, default=_now),
)

# 财报更正留档：同步时发现某报告期的核心数值变了，把被覆盖的旧版本存在这里
# （东财只给最新版本，留档从上线起积累），用于还原"当时披露的数字"
fin_report_revision = Table(
    "fin_report_revision", metadata,
    Column("code", String(16), primary_key=True),
    Column("report_date", Date, primary_key=True),
    Column("revised_at", DateTime(timezone=True), primary_key=True),  # 发现更正的时间
    Column("notice_date", Date, nullable=True),  # 旧版本的公告日
    Column("eps", Float, nullable=True),
    Column("deduct_eps", Float, nullable=True),
    Column("revenue", Float, nullable=True),
    Column("net_profit", Float, nullable=True),
    Column("roe", Float, nullable=True),
    Column("bps", Float, nullable=True),
)

# 三大报表主要科目（东财年报季报），每股每报告期一行，金额单位元。
# 列名 → (报表, 东财字段) 见 STATEMENT_ITEMS；完整科目需逐股取 F10，暂未收录
STATEMENT_ITEMS: dict[str, tuple[str, str, str]] = {
    # 资产负债表
    "total_assets": ("bs", "TOTAL_ASSETS", "总资产"),
    "monetary_funds": ("bs", "MONETARYFUNDS", "货币资金"),
    "accounts_rece": ("bs", "ACCOUNTS_RECE", "应收账款"),
    "inventory": ("bs", "INVENTORY", "存货"),
    "fixed_asset": ("bs", "FIXED_ASSET", "固定资产"),
    "loan_advance": ("bs", "LOAN_ADVANCE", "发放贷款及垫款"),
    "total_liabilities": ("bs", "TOTAL_LIABILITIES", "总负债"),
    "short_loan": ("bs", "SHORT_LOAN", "短期借款"),
    "accounts_payable": ("bs", "ACCOUNTS_PAYABLE", "应付账款"),
    "advance_receivables": ("bs", "ADVANCE_RECEIVABLES", "预收款项"),
    "accept_deposit": ("bs", "ACCEPT_DEPOSIT", "吸收存款"),
    "total_equity": ("bs", "TOTAL_EQUITY", "股东权益"),
    "current_ratio": ("bs", "CURRENT_RATIO", "流动比率 %"),
    "debt_asset_ratio": ("bs", "DEBT_ASSET_RATIO", "资产负债率 %"),
    # 利润表
    "operate_income": ("is", "TOTAL_OPERATE_INCOME", "营业总收入"),
    "operate_total_cost": ("is", "TOTAL_OPERATE_COST", "营业总成本"),
    "operate_cost": ("is", "OPERATE_COST", "营业成本"),
    "sale_expense": ("is", "SALE_EXPENSE", "销售费用"),
    "manage_expense": ("is", "MANAGE_EXPENSE", "管理费用"),
    "finance_expense": ("is", "FINANCE_EXPENSE", "财务费用"),
    "operate_tax": ("is", "OPERATE_TAX_ADD", "税金及附加"),
    "interest_ni": ("is", "INTEREST_NI", "利息净收入"),
    "fee_commission_ni": ("is", "FEE_COMMISSION_NI", "手续费及佣金净收入"),
    "invest_income": ("is", "INVEST_INCOME", "投资收益"),
    "operate_profit": ("is", "OPERATE_PROFIT", "营业利润"),
    "total_profit": ("is", "TOTAL_PROFIT", "利润总额"),
    "income_tax": ("is", "INCOME_TAX", "所得税"),
    "parent_netprofit": ("is", "PARENT_NETPROFIT", "归母净利润"),
    "deduct_netprofit": ("is", "DEDUCT_PARENT_NETPROFIT", "扣非归母净利润"),
    # 现金流量表
    "ocf": ("cf", "NETCASH_OPERATE", "经营活动现金流净额"),
    "sales_cash": ("cf", "SALES_SERVICES", "销售商品收到的现金"),
    "staff_cash": ("cf", "PAY_STAFF_CASH", "支付给职工的现金"),
    "icf": ("cf", "NETCASH_INVEST", "投资活动现金流净额"),
    "capex": ("cf", "CONSTRUCT_LONG_ASSET", "购建长期资产支付的现金"),
    "fcf": ("cf", "NETCASH_FINANCE", "筹资活动现金流净额"),
    "cash_change": ("cf", "CCE_ADD", "现金及等价物净增加额"),
}

fin_statement = Table(
    "fin_statement", metadata,
    Column("code", String(16), primary_key=True),
    Column("report_date", Date, primary_key=True, index=True),
    Column("notice_date", Date, nullable=True),  # 利润表的公告日（三张表一般同时披露）
    *(Column(name, Float, nullable=True) for name in STATEMENT_ITEMS),
)

# 分红送转（东财分红送配），每股每报告期一行。比例都是"每 10 股"
dividend = Table(
    "dividend", metadata,
    Column("code", String(16), primary_key=True),
    Column("report_date", Date, primary_key=True, index=True),
    Column("plan_notice_date", Date, nullable=True),  # 预案公告日
    Column("notice_date", Date, nullable=True),  # 最新公告日
    Column("record_date", Date, nullable=True),  # 股权登记日
    Column("ex_date", Date, nullable=True, index=True),  # 除权除息日
    Column("cash_per10", Float, nullable=True),  # 每 10 股派现（税前，元）
    Column("bonus_per10", Float, nullable=True),  # 每 10 股送股
    Column("transfer_per10", Float, nullable=True),  # 每 10 股转增
    Column("progress", String(32), nullable=False, default=""),  # 预案 / 股东大会通过 / 实施分配…
    Column("plan", String(128), nullable=False, default=""),
)

# 指数成分快照，每个快照日一份全量名单。来源两个：BaoStock（50 / 300 / 500，2005 年起按调整日，
# 无权重）与中证官网（中证系列当期成分与权重，月度快照，从开始采集时积累）。
# 取某日成分 = 不晚于该日的最近一个 snapshot_date 的全部行
index_constituent = Table(
    "index_constituent", metadata,
    Column("index_code", String(16), primary_key=True),
    Column("snapshot_date", Date, primary_key=True),
    Column("code", String(16), primary_key=True, index=True),
    Column("weight", Float, nullable=True),  # 权重 %，只有中证官网的快照有
)

# 主要指数日线（东财），成交量单位手、成交额元
index_bar = Table(
    "index_bar", metadata,
    Column("index_code", String(16), primary_key=True),
    Column("trade_date", Date, primary_key=True),
    Column("open", Float, nullable=False),
    Column("high", Float, nullable=False),
    Column("low", Float, nullable=False),
    Column("close", Float, nullable=False),
    Column("volume", Float, nullable=True),
    Column("amount", Float, nullable=True),
)

# 宏观与利率序列（窄表），indicator 的定义见 kairos_data.sync.MACRO。
# period 为所属期：月度取当月 1 日、季度取季末月 1 日、日度取当日
macro_series = Table(
    "macro_series", metadata,
    Column("indicator", String(32), primary_key=True),
    Column("period", Date, primary_key=True),
    Column("value", Float, nullable=False),
)

# 上市公司公告（东财公告中心）。正文按需抓取；summary 供上层缓存 AI 摘要
announcement = Table(
    "announcement", metadata,
    Column("art_code", String(32), primary_key=True),
    Column("code", String(16), nullable=False, index=True),
    Column("title", String(512), nullable=False),
    Column("category", String(64), nullable=False, default=""),
    Column("ann_date", Date, nullable=False, index=True),
    Column("summary", Text, nullable=True),
    Column("summary_at", DateTime(timezone=True), nullable=True),
)

# 股本变动：每次变动一行（变动日 = 新股本生效日）
share_capital = Table(
    "share_capital", metadata,
    Column("code", String(16), primary_key=True),
    Column("change_date", Date, primary_key=True),
    Column("notice_date", Date, nullable=True),
    Column("total_shares", BigInteger, nullable=True),  # 总股本（股）
    Column("float_a_shares", BigInteger, nullable=True),  # 已上市流通 A 股（股）
    Column("limited_shares", BigInteger, nullable=True),  # 限售股（股）
    Column("change_reason", String(64), nullable=False, default=""),
)

# 股东户数：按统计截止日，定期报告之外还有公司在互动平台披露的中间时点
holder_count = Table(
    "holder_count", metadata,
    Column("code", String(16), primary_key=True),
    Column("end_date", Date, primary_key=True, index=True),
    Column("notice_date", Date, nullable=True),
    Column("holder_num", BigInteger, nullable=True),
    Column("avg_hold_shares", Float, nullable=True),  # 户均持股（股）
    Column("total_a_shares", BigInteger, nullable=True),
)

# 十大股东（kind=all）与十大流通股东（kind=float），按报告期
top_holder = Table(
    "top_holder", metadata,
    Column("code", String(16), primary_key=True),
    Column("end_date", Date, primary_key=True, index=True),
    Column("kind", String(8), primary_key=True),
    Column("rank", Integer, primary_key=True),
    Column("holder_name", String(256), nullable=False, index=True),
    Column("holder_type", String(32), nullable=False, default=""),
    Column("shares_type", String(64), nullable=False, default=""),
    Column("hold_num", BigInteger, nullable=True),  # 持股数（股）
    Column("hold_ratio", Float, nullable=True),  # 占总股本 / 流通股本 %
    Column("change", String(32), nullable=False, default=""),  # 新进 / 增加 / 减少 / 不变
    Column("notice_date", Date, nullable=True),
)

# 限售解禁：每股每解禁日每类限售股一行
lockup = Table(
    "lockup", metadata,
    Column("code", String(16), primary_key=True),
    Column("free_date", Date, primary_key=True, index=True),
    Column("shares_type", String(64), primary_key=True),  # 首发原股东限售 / 定增限售…
    Column("holder_num", Integer, nullable=True),  # 本批涉及股东数
    Column("free_shares", Float, nullable=True),  # 本次解禁（股）
    Column("able_free_shares", Float, nullable=True),  # 实际可流通（股）
    Column("market_cap", Float, nullable=True),  # 解禁市值（元，按公告时股价）
    Column("float_ratio", Float, nullable=True),  # 占解禁前流通股 %
    Column("total_ratio", Float, nullable=True),  # 占总股本 %
)

# 融资融券个股明细（2010 年起），金额单位元
margin_trading = Table(
    "margin_trading", metadata,
    Column("code", String(16), primary_key=True),
    Column("trade_date", Date, primary_key=True, index=True),
    Column("rzye", Float, nullable=True),  # 融资余额
    Column("rzmre", Float, nullable=True),  # 融资买入额
    Column("rzche", Float, nullable=True),  # 融资偿还额
    Column("rqye", Float, nullable=True),  # 融券余额
    Column("rqyl", Float, nullable=True),  # 融券余量（股）
    Column("rqmcl", Float, nullable=True),  # 融券卖出量（股）
    Column("rzrqye", Float, nullable=True),  # 融资融券余额
)

# 龙虎榜：每股每日每个上榜原因一行，金额单位元
billboard = Table(
    "billboard", metadata,
    Column("code", String(16), primary_key=True),
    Column("trade_date", Date, primary_key=True, index=True),
    Column("reason", String(128), primary_key=True),
    Column("close", Float, nullable=True),
    Column("change_pct", Float, nullable=True),
    Column("turnover_rate", Float, nullable=True),
    Column("buy_amt", Float, nullable=True),  # 龙虎榜买入额
    Column("sell_amt", Float, nullable=True),
    Column("net_amt", Float, nullable=True),
    Column("deal_amt", Float, nullable=True),  # 龙虎榜成交额
    Column("deal_ratio", Float, nullable=True),  # 占总成交 %
    Column("comment", String(256), nullable=False, default=""),  # 解读，如"机构买入"
)

# 大宗交易：没有天然唯一键（同买卖方会拆成多笔完全相同的成交），按日期整段替换
block_trade = Table(
    "block_trade", metadata,
    Column("id", Integer, primary_key=True),
    Column("code", String(16), nullable=False, index=True),
    Column("trade_date", Date, nullable=False, index=True),
    Column("price", Float, nullable=True),
    Column("premium_pct", Float, nullable=True),  # 相对收盘价溢价 %
    Column("volume", BigInteger, nullable=True),  # 股
    Column("amount", Float, nullable=True),  # 元
    Column("buyer", String(128), nullable=False, default=""),
    Column("seller", String(128), nullable=False, default=""),
)

# ETF 与可转债清单（股票之外的场内证券）。ETF 来自 BaoStock，可转债来自东财可转债列表
security_info = Table(
    "security_info", metadata,
    Column("code", String(16), primary_key=True),
    Column("exchange", String(8), nullable=False),  # SH / SZ
    Column("sec_type", String(8), nullable=False, index=True),  # etf / cb
    Column("name", String(64), nullable=False),
    Column("list_date", Date, nullable=True),
    Column("delist_date", Date, nullable=True),
    # 以下只有可转债
    Column("stock_code", String(16), nullable=True, index=True),  # 正股
    Column("rating", String(16), nullable=True),
    Column("issue_size", Float, nullable=True),  # 发行规模（亿元）
    Column("maturity_date", Date, nullable=True),
    Column("convert_price", Float, nullable=True),  # 初始转股价
)

# ETF 与可转债日线（不复权），成交量单位手（ETF）/ 张（转债），成交额元
security_bar = Table(
    "security_bar", metadata,
    Column("code", String(16), primary_key=True),
    Column("trade_date", Date, primary_key=True),
    Column("open", Float, nullable=False),
    Column("high", Float, nullable=False),
    Column("low", Float, nullable=False),
    Column("close", Float, nullable=False),
    Column("volume", Float, nullable=True),
    Column("amount", Float, nullable=True),
)

# 同步状态：已完成全量回补的部分（kairos_data.sync.PARTS），回补中断后重启只补没完成的
# 每日指标（BaoStock 日线附带字段），每股每交易日一行，停牌日也有一行。
# 价格不复权；估值按当日收盘价计；总市值 = 收盘价 × 当日适用的总股本（share_capital），无股本数据时为空。
# 北交所 BaoStock 没有，不收录
daily_basic = Table(
    "daily_basic", metadata,
    Column("code", String(16), primary_key=True),
    Column("trade_date", Date, primary_key=True, index=True),
    Column("close", Float, nullable=True),  # 不复权收盘价
    Column("preclose", Float, nullable=True),  # 交易所口径前收（除权日已调整）
    Column("volume", Float, nullable=True),  # 股
    Column("amount", Float, nullable=True),  # 元
    Column("turn", Float, nullable=True),  # 换手率 %（成交量 / 流通股本）
    Column("suspended", Boolean, nullable=False, default=False),
    Column("is_st", Boolean, nullable=False, default=False),  # ST / *ST
    Column("pe_ttm", Float, nullable=True),
    Column("pb_mrq", Float, nullable=True),
    Column("ps_ttm", Float, nullable=True),
    Column("pcf_ttm", Float, nullable=True),  # 市现率（经营现金流 TTM）
    Column("total_mv", Float, nullable=True),  # 总市值（元）
)

sync_state = Table(
    "sync_state", metadata,
    Column("key", String(64), primary_key=True),
    Column("value", String(256), nullable=False, default=""),
    Column("updated_at", DateTime(timezone=True), nullable=False, default=_now),
)
