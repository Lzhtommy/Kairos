"""个股基本面研报 API：登录用户只读共享研报；生成 / 重试 / 删除仅管理员。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import get_current_user, require_admin
from app.models.market import StockInfo
from app.models.research import ResearchReport
from app.models.user import User
from app.services.research_facts import is_financial
from app.services.research_score import SCORING_VERSION

router = APIRouter(prefix="/research", tags=["research"], dependencies=[Depends(get_current_user)])
admin_router = APIRouter(prefix="/admin/research", tags=["admin"], dependencies=[Depends(require_admin)])

MAX_BATCH = 50
ACTIVE = ("queued", "running")


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def _summary(r: ResearchReport) -> dict:
    return {
        "id": r.id,
        "code": r.code,
        "name": r.name,
        "industry": r.industry,
        "grade": r.grade,
        "total_score": r.total_score,
        "coverage": r.coverage,
        "dims": {k: {"label": d["label"], "score": d["score"], "max": d["max"]} for k, d in (r.sub_scores or {}).items()},
        "red_flags": len(r.red_flags or []),
        "data_as_of": r.data_as_of,
        "scoring_version": r.scoring_version,
        "finished_at": _iso(r.finished_at),
    }


def _task(r: ResearchReport) -> dict:
    return {
        "id": r.id,
        "code": r.code,
        "name": r.name,
        "status": r.status,
        "progress": r.progress,
        "error": r.error,
        "grade": r.grade,
        "total_score": r.total_score,
        "created_at": _iso(r.created_at),
        "finished_at": _iso(r.finished_at),
    }


# ---------------------------------------------------------------- 只读


@router.get("")
def list_reports(db: Session = Depends(get_db)):
    """每只股票的当前报告（最新一条 done）。"""
    latest_ids = (
        select(func.max(ResearchReport.id)).where(ResearchReport.status == "done").group_by(ResearchReport.code)
    )
    rows = db.execute(select(ResearchReport).where(ResearchReport.id.in_(latest_ids))).scalars().all()
    updating = set(db.execute(select(ResearchReport.code).where(ResearchReport.status.in_(ACTIVE))).scalars())
    items = [_summary(r) | {"updating": r.code in updating} for r in rows]
    items.sort(key=lambda x: x["total_score"] if x["total_score"] is not None else -1, reverse=True)
    return items


def _get_visible(db: Session, report_id: int, user: User) -> ResearchReport:
    r = db.get(ResearchReport, report_id)
    if r is None or (r.status != "done" and not user.is_admin):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "研报不存在")
    return r


@router.get("/stock/{code}/history")
def stock_history(code: str, db: Session = Depends(get_db)):
    rows = db.execute(
        select(ResearchReport)
        .where(ResearchReport.code == code, ResearchReport.status == "done")
        .order_by(ResearchReport.id)
    ).scalars().all()
    return [
        {"id": r.id, "total_score": r.total_score, "grade": r.grade, "data_as_of": r.data_as_of,
         "scoring_version": r.scoring_version, "finished_at": _iso(r.finished_at)}
        for r in rows
    ]


@router.get("/{report_id}")
def get_report(report_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = _get_visible(db, report_id, user)
    updating = db.execute(
        select(func.count()).select_from(ResearchReport).where(
            ResearchReport.code == r.code, ResearchReport.status.in_(ACTIVE)
        )
    ).scalar()
    return _summary(r) | {
        "status": r.status,
        "updating": bool(updating),
        "sub_scores": r.sub_scores,
        "red_flags": r.red_flags,
        "sections": r.sections,
        "facts": r.facts,
        "sources": r.sources,
        "llm_model": r.llm_model,
        "created_at": _iso(r.created_at),
    }


@router.get("/{report_id}/markdown", response_class=PlainTextResponse)
def get_markdown(report_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _get_visible(db, report_id, user).content_md


# ---------------------------------------------------------------- 管理


class GenerateIn(BaseModel):
    codes: list[str] = Field(min_length=1)
    force: bool = False


def _cst_day_start() -> datetime:
    now_cst = datetime.now(timezone.utc) + timedelta(hours=8)
    return datetime(now_cst.year, now_cst.month, now_cst.day, tzinfo=timezone.utc) - timedelta(hours=8)


@admin_router.post("")
def generate(body: GenerateIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    codes: list[str] = []
    for raw in body.codes:
        for c in re.findall(r"\d{6}", raw):
            if c not in codes:
                codes.append(c)
    if not codes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "没有识别到 6 位股票代码")
    if len(codes) > MAX_BATCH:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"单次最多提交 {MAX_BATCH} 只")

    day_start = _cst_day_start()
    results = []
    for code in codes:
        info = db.get(StockInfo, code)
        if info is None:
            results.append({"code": code, "ok": False, "reason": "代码不存在"})
            continue
        if is_financial(info.industry):
            results.append({"code": code, "name": info.name, "ok": False, "reason": "金融类暂不支持"})
            continue
        active = db.execute(
            select(func.count()).select_from(ResearchReport).where(
                ResearchReport.code == code, ResearchReport.status.in_(ACTIVE)
            )
        ).scalar()
        if active:
            results.append({"code": code, "name": info.name, "ok": False, "reason": "已在队列中"})
            continue
        if not body.force:
            today = db.execute(
                select(func.count()).select_from(ResearchReport).where(
                    ResearchReport.code == code,
                    ResearchReport.status == "done",
                    ResearchReport.scoring_version == SCORING_VERSION,
                    ResearchReport.created_at >= day_start,
                )
            ).scalar()
            if today:
                results.append({"code": code, "name": info.name, "ok": False, "reason": "今日已生成，可勾选强制重新生成"})
                continue
        r = ResearchReport(
            code=code, name=info.name, industry=info.industry, status="queued",
            created_by=admin.id, scoring_version=SCORING_VERSION,
        )
        db.add(r)
        db.flush()
        results.append({"code": code, "name": info.name, "ok": True, "id": r.id})
    db.commit()
    return results


@admin_router.get("/tasks")
def list_tasks(limit: int = 100, db: Session = Depends(get_db)):
    rows = db.execute(
        select(ResearchReport).order_by(ResearchReport.id.desc()).limit(max(1, min(limit, 500)))
    ).scalars().all()
    return [_task(r) for r in rows]


@admin_router.post("/{report_id}/retry")
def retry(report_id: int, db: Session = Depends(get_db)):
    r = db.get(ResearchReport, report_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    if r.status != "failed":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "只有失败的任务可以重试")
    r.status, r.progress, r.error, r.finished_at = "queued", "", None, None
    db.commit()
    return _task(r)


@admin_router.delete("/{report_id}")
def delete(report_id: int, db: Session = Depends(get_db)):
    r = db.get(ResearchReport, report_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "研报不存在")
    if r.status == "running":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "运行中的任务不能删除")
    db.delete(r)
    db.commit()
    return {"ok": True}
