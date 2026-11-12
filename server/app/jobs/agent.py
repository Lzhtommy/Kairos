"""AI 操盘手调度：盘前分析、盘后结算 + 复盘、周度反思。

全部幂等：同一账户同一天同一类运行只跑一次（失败的也不自动重跑，避免反复烧 token，
管理员可在页面手动触发）。账户串行执行，ECS 内存小，不并发。
结算不依赖 LLM：没配模型时账户照常按日撮合挂单、盯市。
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select, update

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.agent import AgentAccount, AgentSession
from app.models.market import Kline
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


def agent_postclose(force: bool = False) -> None:
    """15:45 CST：先结算当日撮合，再让 agent 复盘。启动时补跑（只在当日收盘后生效）。"""
    if not settings.enable_agent:
        return
    db = SessionLocal()
    try:
        accounts = _accounts(db)
        if not accounts:
            return
        for acct in accounts:
            try:
                broker.settle_due(db, acct)
            except Exception:  # noqa: BLE001 — 单账户结算失败不拦其他账户
                logger.exception("agent settle failed: acct %s", acct.id)
                db.rollback()
        latest = db.execute(select(Kline.ts).where(Kline.period == "1d").order_by(Kline.ts.desc()).limit(1)).scalar()
        today = broker.cst_today()
        now = broker.cst_now()
        if not force and (latest != today or (now.hour, now.minute) < (15, 10)):
            return  # 非交易日或尚未收盘：只补结算，不复盘
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
