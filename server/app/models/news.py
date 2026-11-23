"""全球快讯（多源 7×24 轮询，见 app/services/news.py）。

每条快讯按源原样落一行，(source, source_id) 唯一；同一事件被多家报道时
共用一个 event_id（取该事件最早入库那条的 id）。

seen_at 是 Kairos **首次抓到**的时刻（UTC），pub_at 是源自己标的发布时间。
前向研究以 seen_at 为准——它是当时真实可得的时点；服务启动首轮抓到的
是接口里积压的旧条目，seen_at 会明显晚于 pub_at。
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NewsItem(Base):
    __tablename__ = "news_item"
    __table_args__ = (UniqueConstraint("source", "source_id", name="uq_news_source_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(8))         # cls / jin10 / wscn / sina / ths
    source_id: Mapped[str] = mapped_column(String(40))
    pub_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 0 普通 / 1 重要 / 2 特别重要（各源自带标记归一，口径见 services/news.py）
    importance: Mapped[int] = mapped_column(SmallInteger, default=0)
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # 源自带的关联 A 股（同花顺 / 财联社），[{"code": "600160", "name": "巨化股份"}]
    stocks: Mapped[list | None] = mapped_column(JSON, nullable=True)


class NewsEvent(Base):
    """事件级 LLM 标注（services/news_classify.py）：分类、1-5 重要度、相关申万行业。

    事件第一次出现后一分钟内批量打标；后续加入的报道源不再重打。
    没有这一行 = 还没打标或 LLM 不可用，前端按源标记规则兜底。

    parent_id 非空 = LLM 判定是更早某个事件的后续（同一讲话的另一句、同一波行情的进一步
    变化、合并没认出来的他家报道），不单独显示，挂在父事件下面。父事件的 score 是整串的最高分。
    """

    __tablename__ = "news_event"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    category: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    sectors: Mapped[list] = mapped_column(JSON, default=list)
    model: Mapped[str] = mapped_column(String(32), default="")
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # 父事件：整串（自身 + 后续）的报道家数，挂后续时更新。一句一条的讲话单看每条只有一家，
    # 整串往往五家都报了
    thread_sources: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
