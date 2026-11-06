"""金融数据仓库（"小万得"）里现有表覆盖不到的数据：财报、分红、指数成分、宏观、公告。

股票池（stock_info，含退市股）和日 K（kline）沿用原表，这里不重复存。
事实表都带可得日期（公告日 / 实施日），查阅时能看到"当时市场知道什么"。
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FinReport(Base):
    """定期报告主要指标（东财业绩报表），每股每报告期一行，保留最新披露版本。"""

    __tablename__ = "fin_report"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, primary_key=True, index=True)  # 报告期末
    notice_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)  # 公告日
    eps: Mapped[float | None] = mapped_column(Float, nullable=True)
    deduct_eps: Mapped[float | None] = mapped_column(Float, nullable=True)  # 扣非每股收益
    revenue: Mapped[float | None] = mapped_column(Float, nullable=True)  # 营业总收入（元）
    net_profit: Mapped[float | None] = mapped_column(Float, nullable=True)  # 归母净利润（元）
    revenue_yoy: Mapped[float | None] = mapped_column(Float, nullable=True)  # %
    profit_yoy: Mapped[float | None] = mapped_column(Float, nullable=True)  # %
    roe: Mapped[float | None] = mapped_column(Float, nullable=True)  # 加权 ROE %
    bps: Mapped[float | None] = mapped_column(Float, nullable=True)  # 每股净资产
    ocf_ps: Mapped[float | None] = mapped_column(Float, nullable=True)  # 每股经营现金流
    gross_margin: Mapped[float | None] = mapped_column(Float, nullable=True)  # 销售毛利率 %
    dividend_plan: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Dividend(Base):
    """分红送转（东财分红送配），每股每报告期一行。比例都是"每 10 股"。"""

    __tablename__ = "dividend"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
    plan_notice_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # 预案公告日
    notice_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # 最新公告日
    record_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # 股权登记日
    ex_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)  # 除权除息日
    cash_per10: Mapped[float | None] = mapped_column(Float, nullable=True)  # 每 10 股派现（税前，元）
    bonus_per10: Mapped[float | None] = mapped_column(Float, nullable=True)  # 每 10 股送股
    transfer_per10: Mapped[float | None] = mapped_column(Float, nullable=True)  # 每 10 股转增
    progress: Mapped[str] = mapped_column(String(32), default="")  # 预案 / 股东大会通过 / 实施分配…
    plan: Mapped[str] = mapped_column(String(128), default="")


class IndexConstituent(Base):
    """指数成分快照（BaoStock），每个成分调整日一份全量名单。

    取某日成分 = 不晚于该日的最近一个 snapshot_date 的全部行。
    """

    __tablename__ = "index_constituent"

    index_code: Mapped[str] = mapped_column(String(16), primary_key=True)  # 000300 / 000905 / 000016
    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), primary_key=True, index=True)


class MacroSeries(Base):
    """宏观与利率序列，窄表：indicator 见 app.jobs.warehouse.MACRO 的定义。

    period 为所属期（月度取当月 1 日、季度取季末月 1 日、日度取当日），
    数据源不提供发布日，月度数据一般在次月中旬公布。
    """

    __tablename__ = "macro_series"

    indicator: Mapped[str] = mapped_column(String(32), primary_key=True)
    period: Mapped[date] = mapped_column(Date, primary_key=True)
    value: Mapped[float] = mapped_column(Float)


class Announcement(Base):
    """上市公司公告（东财公告中心），正文按需抓取，AI 摘要按需生成并缓存。"""

    __tablename__ = "announcement"

    art_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str] = mapped_column(String(512))
    category: Mapped[str] = mapped_column(String(64), default="")
    ann_date: Mapped[date] = mapped_column(Date, index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
