"""数据库表结构（SQLAlchemy Core）：股票池、日 K、财报、分红、指数成分、宏观、公告。

单独使用时 `metadata.create_all(engine)` 建表；嵌入 Kairos 时由 app.models 把这些表
复制进业务库的 metadata 并映射 ORM 类，两边共用同一份定义。
Float / DateTime 在 MySQL 下的编译口径见 kairos_data.store。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (BigInteger, Column, Date, DateTime, Float, Index, Integer, MetaData, String,
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

# 日 K（前复权，成交量单位：手）
kline = Table(
    "kline", metadata,
    Column("id", Integer, primary_key=True),
    Column("code", String(16), nullable=False, index=True),
    Column("period", String(8), nullable=False, default="1d"),
    Column("ts", DateTime(timezone=True), nullable=False, index=True),
    Column("open", Float, nullable=False),
    Column("high", Float, nullable=False),
    Column("low", Float, nullable=False),
    Column("close", Float, nullable=False),
    Column("volume", BigInteger, nullable=False),
    # 按股取最近 N 根的复合索引
    Index("ix_kline_code_period_ts", "code", "period", "ts"),
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

# 指数成分快照（BaoStock），每个成分调整日一份全量名单。
# 取某日成分 = 不晚于该日的最近一个 snapshot_date 的全部行
index_constituent = Table(
    "index_constituent", metadata,
    Column("index_code", String(16), primary_key=True),  # 000300 / 000905 / 000016
    Column("snapshot_date", Date, primary_key=True),
    Column("code", String(16), primary_key=True, index=True),
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
