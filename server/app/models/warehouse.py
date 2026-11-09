"""数据仓库表的 ORM 映射：财报、分红、股本股东、指数成分、宏观、公告。

表结构定义在 kairos_data.schema（独立的采集包），这里复制进业务库的 metadata，
Alembic 和查阅接口照常用 ORM 类。
"""

from __future__ import annotations

from app.core.db import Base
from kairos_data import schema


class FinReport(Base):
    __table__ = schema.fin_report.to_metadata(Base.metadata)


class FinStatement(Base):
    __table__ = schema.fin_statement.to_metadata(Base.metadata)


class Dividend(Base):
    __table__ = schema.dividend.to_metadata(Base.metadata)


class IndexConstituent(Base):
    __table__ = schema.index_constituent.to_metadata(Base.metadata)


class IndexBar(Base):
    __table__ = schema.index_bar.to_metadata(Base.metadata)


class MacroSeries(Base):
    __table__ = schema.macro_series.to_metadata(Base.metadata)


class Announcement(Base):
    __table__ = schema.announcement.to_metadata(Base.metadata)


class ShareCapital(Base):
    __table__ = schema.share_capital.to_metadata(Base.metadata)


class HolderCount(Base):
    __table__ = schema.holder_count.to_metadata(Base.metadata)


class TopHolder(Base):
    __table__ = schema.top_holder.to_metadata(Base.metadata)


class Lockup(Base):
    __table__ = schema.lockup.to_metadata(Base.metadata)


class MarginTrading(Base):
    __table__ = schema.margin_trading.to_metadata(Base.metadata)


class Billboard(Base):
    __table__ = schema.billboard.to_metadata(Base.metadata)


class BlockTrade(Base):
    __table__ = schema.block_trade.to_metadata(Base.metadata)
