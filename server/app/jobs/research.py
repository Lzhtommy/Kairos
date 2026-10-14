"""研报任务队列：APScheduler 定时取 1 条 queued 串行执行（ECS 内存小，不并发）。

容器重启时残留的 running 重置回 queued，任务不丢。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.core.db import SessionLocal
from app.models.research import ResearchReport
from app.services.research import run_report

logger = logging.getLogger("kairos.research")


def requeue_interrupted() -> None:
    db = SessionLocal()
    try:
        n = db.execute(
            update(ResearchReport).where(ResearchReport.status == "running").values(status="queued", progress="")
        ).rowcount
        db.commit()
        if n:
            logger.info("Requeued %d interrupted research reports", n)
    finally:
        db.close()


def process_research_queue() -> None:
    db = SessionLocal()
    try:
        report = db.execute(
            select(ResearchReport).where(ResearchReport.status == "queued").order_by(ResearchReport.id).limit(1)
        ).scalar_one_or_none()
        if report is None:
            return
        report.status, report.progress = "running", "开始"
        db.commit()
        logger.info("Research report #%d (%s) started", report.id, report.code)
        try:
            run_report(db, report)
            logger.info("Research report #%d done: %s %s", report.id, report.total_score, report.grade)
        except Exception as exc:  # noqa: BLE001 — 标失败，队列继续
            db.rollback()
            report = db.get(ResearchReport, report.id)
            if report is not None:
                report.status = "failed"
                report.error = str(exc)[:500]
                report.finished_at = datetime.now(timezone.utc)
                db.commit()
            logger.warning("Research report failed: %s", exc)
    finally:
        db.close()
