"""AI 操盘手调度：08:50 盘前分析、09:26 开盘执行、15:45 结算、20:30 盘后复盘、周六周度反思。

全部幂等：同一账户同一天同一类运行只跑一次（失败的也不自动重跑，避免反复烧 token，
管理员可在页面手动触发）。账户串行执行，ECS 内存小，不并发。
结算不依赖 LLM：没配模型时账户照常按日撮合挂单、盯市。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.agent import AgentAccount, AgentSession
from app.models.market import Kline, Quote
from app.services import agent as agent_svc
from app.services import agent_broker as broker
from app.services import llm

logger = logging.getLogger("kairos.agent")


def mark_interrupted() -> None:
    """容器重启时残留的 running 运行标失败（agent 对话无法从中途恢复）。"""
    db = SessionLocal()
    try:
        n = db.execute(
            update(AgentSession).where(AgentSession.status == "running")
            .values(status="failed", error="服务重启，运行中断")
        ).rowcount
        db.commit()
        if n:
            logger.info("Marked %d interrupted agent sessions as failed", n)
    finally:
        db.close()


def _ran(db, acct_id: int, kind: str, since) -> bool:
    return db.execute(
        select(AgentSession.id).where(
            AgentSession.account_id == acct_id, AgentSession.kind == kind, AgentSession.trade_date >= since
        ).limit(1)
    ).scalar() is not None


def _accounts(db) -> list[AgentAccount]:
    return list(db.execute(select(AgentAccount).where(AgentAccount.enabled.is_(True))).scalars())


def agent_premarket(force: bool = False) -> None:
    """08:50 CST：当天下的单按今日行情撮合。节假日会白跑一轮（挂单顺延到下个交易日撮合）。"""
    if not settings.enable_agent or not llm.enabled():
        return
    now = broker.cst_now()
    if not force and (now.weekday() >= 5 or not (8, 0) <= (now.hour, now.minute) < (9, 30)):
        return
    today = broker.cst_today()
    db = SessionLocal()
    try:
        for acct in _accounts(db):
            if _ran(db, acct.id, "premarket", today) or agent_svc.running_session(db, acct.id):
                continue
            agent_svc.run_session(db, acct, "premarket")
    finally:
        db.close()


OPEN_WINDOW = ((9, 25), (9, 29))  # 开盘执行会话允许启动的时间段（CST）


def auction_ready(db) -> bool:
    """行情快照是否已是今日集合竞价之后的（09:25 后采到、带开盘价）。节假日 / 采集掉线时为 False。"""
    today = broker.cst_today()
    since = datetime(today.year, today.month, today.day, 9, 25, tzinfo=timezone.utc) - timedelta(hours=8)
    rows = db.execute(select(Quote.ts, Quote.open).limit(200)).all()
    fresh = [o for ts, o in rows if ts is not None and broker._aware(ts) >= since]
    return len(fresh) >= len(rows) * 0.8 > 0 and sum(1 for o in fresh if o) >= len(fresh) * 0.8


def agent_open(force: bool = False) -> None:
    """09:26 CST：集合竞价结束后按盘前计划执行（此时能看到今日开盘价）。

    行情快照不是今日竞价之后的（节假日、采集掉线）就不跑——此时看到的 open 是昨天的，执行会误判。
    """
    if not settings.enable_agent or not llm.enabled():
        return
    now = broker.cst_now()
    if not force and (now.weekday() >= 5 or not OPEN_WINDOW[0] <= (now.hour, now.minute) <= OPEN_WINDOW[1]):
        return
    db = SessionLocal()
    try:
        if not auction_ready(db):
            logger.warning("Agent open session skipped: quotes are not from today's auction yet")
            return
        today = broker.cst_today()
        for acct in _accounts(db):
            if _ran(db, acct.id, "open", today) or agent_svc.running_session(db, acct.id):
                continue
            agent_svc.run_session(db, acct, "open")
    finally:
        db.close()


def agent_settle() -> None:
    """15:45 CST：按当日 K 线撮合结算，不依赖 LLM。启动时补跑（幂等，停机后自动补日）。"""
    if not settings.enable_agent:
        return
    db = SessionLocal()
    try:
        for acct in _accounts(db):
            try:
                broker.settle_due(db, acct)
            except Exception:  # noqa: BLE001 — 单账户结算失败不拦其他账户
                logger.exception("agent settle failed: acct %s", acct.id)
                db.rollback()
    finally:
        db.close()


REVIEW_FROM = (20, 0)  # 盘后复盘最早时刻（CST）：公告、行业数据、龙虎榜到这时基本齐了


def agent_postclose(force: bool = False) -> None:
    """20:30 CST：盘后复盘（只读，不下单）。先补一次结算；启动时补跑（只在当日 20:00 后生效）。"""
    if not settings.enable_agent:
        return
    agent_settle()
    db = SessionLocal()
    try:
        accounts = _accounts(db)
        if not accounts:
            return
        latest = db.execute(select(Kline.ts).where(Kline.period == "1d").order_by(Kline.ts.desc()).limit(1)).scalar()
        today = broker.cst_today()
        now = broker.cst_now()
        if not force and (latest != today or (now.hour, now.minute) < REVIEW_FROM):
            return  # 非交易日或还没到复盘时间：只补结算
        if not llm.enabled():
            return
        for acct in accounts:
            if acct.last_settled != today and not force:
                continue  # 结算没跟上（如当日 K 线缺失），复盘拿不到当日结果
            if _ran(db, acct.id, "postclose", today) or agent_svc.running_session(db, acct.id):
                continue
            agent_svc.run_session(db, acct, "postclose")
    finally:
        db.close()


def agent_weekly_review(force: bool = False) -> None:
    """周六 10:00 CST：周度反思，评估经验并改写交易手册。"""
    if not settings.enable_agent or not llm.enabled():
        return
    now = broker.cst_now()
    if not force and now.weekday() != 5:
        return
    since = broker.cst_today() - timedelta(days=6)
    db = SessionLocal()
    try:
        for acct in _accounts(db):
            if _ran(db, acct.id, "review", since) or agent_svc.running_session(db, acct.id):
                continue
            agent_svc.run_session(db, acct, "review")
    finally:
        db.close()
