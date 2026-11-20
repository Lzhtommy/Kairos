"""全球快讯 API。快讯正文来自第三方财经媒体，需登录才能看，页面只展示摘录并链回原文。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import get_current_user
from app.services import news

router = APIRouter(prefix="/news", tags=["news"], dependencies=[Depends(get_current_user)])


@router.get("/events")
def events(
    limit: int = Query(50, ge=1, le=200),
    before: int | None = Query(None, description="翻页游标：上一页最后一个事件的 id"),
    important: bool = False,
    q: str | None = Query(None, max_length=50),
    db: Session = Depends(get_db),
):
    items = news.list_events(db, limit=limit, before=before, important=important, q=(q or "").strip() or None)
    return {"events": items, "next": items[-1]["id"] if len(items) == limit else None}


@router.get("/sources")
def sources(db: Session = Depends(get_db)):
    return news.source_status(db)
