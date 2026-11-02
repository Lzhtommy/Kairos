from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Watchlist(Base):
    __tablename__ = "watchlists"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64), default="默认自选")
    is_default: Mapped[bool] = mapped_column(default=True)


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("watchlist_id", "stock_code", name="uq_watchlist_stock"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(
        ForeignKey("watchlists.id", ondelete="CASCADE"), index=True
    )
    stock_code: Mapped[str] = mapped_column(String(16))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class WatchlistImport(Base):
    """一次自选股导入（一个交易日的下单文件里标记为买入的股票）。

    同一用户同一交易日重复导入会覆盖旧批次，见 api/watchlist.py。
    """

    __tablename__ = "watchlist_imports"
    __table_args__ = (UniqueConstraint("user_id", "trade_date", name="uq_watchlist_import_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    trade_date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    filename: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class WatchlistImportItem(Base):
    __tablename__ = "watchlist_import_items"
    __table_args__ = (UniqueConstraint("import_id", "stock_code", name="uq_watchlist_import_stock"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    import_id: Mapped[int] = mapped_column(
        ForeignKey("watchlist_imports.id", ondelete="CASCADE"), index=True
    )
    stock_code: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[int] = mapped_column(Integer, default=0)
