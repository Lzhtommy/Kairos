"""快讯要闻推送到飞书机器人。每轮 LLM 标注后调用。

两档（用户在设置页选）：
- top：只推 5 分的重大事件（2026-11-23 那天全天 4 条，如英伟达董事会恢复黄仁勋职务）
- important：再加 4 分且 4 家以上报道的（全天约二三十条）

只推根事件（后续挂在父事件下，不单推）。报道家数会随时间增加，所以每轮都重新看近
WINDOW 内还没推过的事件，够格就推；过了窗口还不够格的就不推了——晚到的消息已失去及时性。
每个事件只推一次：够格那一轮按当时的档位发给所有对应用户，然后写 pushed_at。
发送失败只记日志，不重试，避免一条消息反复轰炸。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.news import NewsEvent, NewsItem
from app.models.user import UserSetting
from app.services import notify
from app.services.news import _build_event, _load

logger = logging.getLogger("kairos.news")

LEVELS = ("off", "top", "important")
WINDOW = timedelta(minutes=30)
CST = timezone(timedelta(hours=8))
PAGE_URL = "https://kairos.ren/app/news"


def is_feishu_webhook(url: str) -> bool:
    return url.startswith(("https://open.feishu.cn/open-apis/bot/", "https://open.larksuite.com/open-apis/bot/"))


def tier(event: dict, thread_sources: int | None) -> str | None:
    """事件够哪一档：top / important / None。"""
    score = event["score"]
    if score is None:
        return None
    n = max(len({s["source"] for s in event["sources"]}), thread_sources or 0)
    if score >= 5:
        return "top"
    if score >= 4 and n >= 4:
        return "important"
    return None


def format_message(event: dict, level: str) -> tuple[str, str]:
    t = datetime.fromisoformat(event["pub_at"].replace("Z", "+00:00")).astimezone(CST)
    tag = "重大" if level == "top" else "要闻"
    head = event["title"] or event["content"][:60]
    title = f"【Kairos {tag}】{t:%H:%M} {head}"
    body = event["content"] if event["title"] else event["content"][60:]
    names = "、".join(dict.fromkeys(s["name"] for s in event["sources"]))
    lines = [body[:300].strip()] if body.strip() else []
    lines.append(f"{event['category'] or '未分类'} · {names}")
    lines.append(PAGE_URL)
    return title, "\n".join(lines)


def push_pending(db: Session, now: datetime | None = None) -> int:
    """推送一轮，返回推出去的事件数。"""
    now = now or datetime.now(timezone.utc)
    subs = db.execute(
        select(UserSetting.user_id, UserSetting.news_push_level, UserSetting.news_webhook)
        .where(UserSetting.news_push_level != "off", UserSetting.news_webhook != "")
    ).all()
    if not subs:
        return 0
    pub = func.min(NewsItem.pub_at)
    ids = list(db.scalars(
        select(NewsEvent.event_id)
        .join(NewsItem, NewsItem.event_id == NewsEvent.event_id)
        .where(NewsEvent.parent_id.is_(None), NewsEvent.pushed_at.is_(None), NewsEvent.score >= 4)
        .group_by(NewsEvent.event_id)
        .having(pub >= now - WINDOW)
        .order_by(NewsEvent.event_id)
    ))
    if not ids:
        return 0
    groups, anns = _load(db, ids)
    sent = 0
    for i in ids:
        if i not in groups:
            continue
        ann = anns[i]
        ev = _build_event(groups[i], ann)
        level = tier(ev, ann.thread_sources)
        if level is None:
            continue
        title, text = format_message(ev, level)
        for uid, user_level, url in subs:
            if level == "top" or user_level == "important":
                ok = notify.send("webhook", url, title, text)
                if not ok:
                    logger.warning("news push to user %s failed (event %s)", uid, i)
        ann.pushed_at = now
        sent += 1
    db.commit()
    if sent:
        logger.info("news push: %d events to %d subscribers", sent, len(subs))
    return sent
