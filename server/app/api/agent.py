"""AI 操盘手 API：登录用户只读围观（持仓、净值、订单、决策日志、经验库、手册版本）；
建账户 / 启停 / 手动运行 / 删除仅管理员。"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, get_db
from app.core.security import get_current_user, require_admin
from app.models.agent import (AgentAccount, AgentEquity, AgentMemory, AgentOrder, AgentPlaybook,
                              AgentPosition, AgentSession)
from app.models.user import User
from app.services import agent as agent_svc
from app.services import agent_broker as broker
from app.services import agent_tools as tools
from app.services import llm

router = APIRouter(prefix="/agent", tags=["agent"], dependencies=[Depends(get_current_user)])
admin_router = APIRouter(prefix="/admin/agent", tags=["admin"], dependencies=[Depends(require_admin)])


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def _day(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%d") if dt else None


def _account(db: Session, account_id: int) -> AgentAccount:
    acct = db.get(AgentAccount, account_id)
    if acct is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "账户不存在")
    return acct


def _summary(db: Session, acct: AgentAccount) -> dict:
    last = db.execute(
        select(AgentEquity).where(AgentEquity.account_id == acct.id).order_by(AgentEquity.date.desc()).limit(1)
    ).scalar()
    first_bench = db.execute(
        select(AgentEquity.bench_close).where(AgentEquity.account_id == acct.id, AgentEquity.bench_close.is_not(None))
        .order_by(AgentEquity.date).limit(1)
    ).scalar()
    total = last.total if last else acct.cash
    bench_ret = (
        round((last.bench_close / first_bench - 1) * 100, 2) if last and last.bench_close and first_bench else None
    )
    session = db.execute(
        select(AgentSession).where(AgentSession.account_id == acct.id).order_by(AgentSession.id.desc()).limit(1)
    ).scalar()
    return {
        "id": acct.id,
        "name": acct.name,
        "enabled": acct.enabled,
        "initialCash": acct.initial_cash,
        "total": round(total, 2),
        "returnPct": round((total / acct.initial_cash - 1) * 100, 2),
        "benchmark": acct.benchmark,
        "benchReturnPct": bench_ret,
        "lastSettled": _day(acct.last_settled),
        "playbookVersion": acct.playbook_version,
        "lastSession": _session_row(session) if session else None,
        "createdAt": _iso(acct.created_at),
    }


def _session_row(s: AgentSession) -> dict:
    return {
        "id": s.id,
        "kind": s.kind,
        "kindLabel": agent_svc.KIND_LABELS.get(s.kind, s.kind),
        "tradeDate": _day(s.trade_date),
        "status": s.status,
        "preview": " ".join((s.summary or "").split())[:160],
        "steps": len(s.steps or []),
        "tokens": s.tokens,
        "model": s.model,
        "error": s.error,
        "startedAt": _iso(s.started_at),
        "finishedAt": _iso(s.finished_at),
    }


def _order_public(o: AgentOrder) -> dict:
    return {
        "id": o.id, "code": o.code, "name": o.name, "side": o.side, "shares": o.shares,
        "limitPrice": o.limit_price, "status": o.status, "tradeDate": _day(o.trade_date),
        "filledShares": o.filled_shares, "fillPrice": o.fill_price, "amount": o.amount, "fee": o.fee,
        "realizedPnl": o.realized_pnl, "note": o.note, "reason": o.reason, "memoryIds": o.memory_ids or [],
        "sessionId": o.session_id, "createdAt": _iso(o.created_at),
    }


# ---------------------------------------------------------------- 只读


@router.get("/accounts")
def list_accounts(db: Session = Depends(get_db)):
    accts = db.execute(select(AgentAccount).order_by(AgentAccount.id)).scalars().all()
    return {
        "llmEnabled": llm.enabled(),
        "model": llm.model_name(),
        "items": [_summary(db, a) for a in accts],
    }


@router.get("/accounts/{account_id}")
def account_detail(account_id: int, db: Session = Depends(get_db)):
    acct = _account(db, account_id)
    eq = db.execute(
        select(AgentEquity).where(AgentEquity.account_id == acct.id).order_by(AgentEquity.date)
    ).scalars().all()
    base_bench = next((p.bench_close for p in eq if p.bench_close), None)
    curve = [{"t": _day(p.date), "v": round(p.total / acct.initial_cash, 4)} for p in eq]
    bench = [
        {"t": _day(p.date), "v": round(p.bench_close / base_bench, 4)} for p in eq if p.bench_close and base_bench
    ]
    orders = db.execute(
        select(AgentOrder).where(AgentOrder.account_id == acct.id).order_by(AgentOrder.id.desc()).limit(200)
    ).scalars().all()
    memories = db.execute(
        select(AgentMemory).where(AgentMemory.account_id == acct.id)
        .order_by(AgentMemory.active.desc(), AgentMemory.id.desc()).limit(200)
    ).scalars().all()
    stats = agent_svc.review_stats(db, acct)
    outcomes = stats.pop("memoryOutcomes")
    ctx = tools.Ctx(db=db, account=acct, session=None, can_trade=False, can_edit_playbook=False)  # type: ignore[arg-type]
    fees = db.execute(select(func.sum(AgentOrder.fee)).where(AgentOrder.account_id == acct.id)).scalar() or 0.0
    return {
        **_summary(db, acct),
        "portfolio": tools.portfolio(ctx),
        "curve": curve,
        "benchCurve": bench,
        "stats": {**stats, "totalFees": round(fees, 2)},
        "fees": broker.fees_of(acct),
        "playbook": acct.playbook,
        "orders": [_order_public(o) for o in orders],
        "memories": [
            {
                **tools.memory_row(m),
                "active": m.active,
                "retireReason": m.retire_reason,
                "outcome": outcomes.get(m.id),
            }
            for m in memories
        ],
        "running": bool(agent_svc.running_session(db, acct.id)),
    }


@router.get("/accounts/{account_id}/sessions")
def list_sessions(account_id: int, limit: int = 60, db: Session = Depends(get_db)):
    _account(db, account_id)
    rows = db.execute(
        select(AgentSession).where(AgentSession.account_id == account_id)
        .order_by(AgentSession.id.desc()).limit(max(1, min(limit, 200)))
    ).scalars().all()
    return [_session_row(s) for s in rows]


@router.get("/sessions/{session_id}")
def session_detail(session_id: int, db: Session = Depends(get_db)):
    s = db.get(AgentSession, session_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "运行记录不存在")
    orders = db.execute(select(AgentOrder).where(AgentOrder.session_id == s.id).order_by(AgentOrder.id)).scalars()
    return {**_session_row(s), "summary": s.summary, "trace": s.steps or [],
            "orders": [_order_public(o) for o in orders]}


@router.get("/accounts/{account_id}/playbooks")
def list_playbooks(account_id: int, db: Session = Depends(get_db)):
    _account(db, account_id)
    rows = db.execute(
        select(AgentPlaybook).where(AgentPlaybook.account_id == account_id).order_by(AgentPlaybook.version.desc())
    ).scalars().all()
    return [
        {"version": p.version, "content": p.content, "rationale": p.rationale, "sessionId": p.session_id,
         "createdAt": _iso(p.created_at)}
        for p in rows
    ]


# ---------------------------------------------------------------- 管理


class CreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    initialCash: float = Field(default=1_000_000, ge=10_000, le=1e9)
    benchmark: str = Field(default="000300", pattern=r"^(000300|000905|000852|399006)$")


class PatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None


class RunIn(BaseModel):
    kind: str = Field(default="manual", pattern=r"^(manual|premarket|postclose|review)$")


@admin_router.post("/accounts")
def create_account(body: CreateIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    acct = agent_svc.create_account(db, body.name.strip(), body.initialCash, admin.id, body.benchmark)
    return _summary(db, acct)


@admin_router.patch("/accounts/{account_id}")
def patch_account(account_id: int, body: PatchIn, db: Session = Depends(get_db)):
    acct = _account(db, account_id)
    if body.name is not None:
        acct.name = body.name.strip()
    if body.enabled is not None:
        acct.enabled = body.enabled
    db.commit()
    return _summary(db, acct)


@admin_router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(account_id: int, db: Session = Depends(get_db)):
    acct = _account(db, account_id)
    if agent_svc.running_session(db, acct.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "正在运行，结束后再删除")
    for model in (AgentOrder, AgentEquity, AgentSession, AgentMemory, AgentPlaybook, AgentPosition):
        db.query(model).filter(model.account_id == acct.id).delete()  # SQLite 默认不级联外键
    db.delete(acct)
    db.commit()


def _run_in_background(account_id: int, kind: str) -> None:
    db = SessionLocal()
    try:
        acct = db.get(AgentAccount, account_id)
        if acct is None:
            return
        if kind == "postclose":
            broker.settle_due(db, acct)
        agent_svc.run_session(db, acct, kind)
    finally:
        db.close()


@admin_router.post("/accounts/{account_id}/run", status_code=status.HTTP_202_ACCEPTED)
def run_now(account_id: int, body: RunIn, db: Session = Depends(get_db)):
    acct = _account(db, account_id)
    if not llm.enabled():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "LLM 未配置")
    if agent_svc.running_session(db, acct.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "该账户已有运行中的任务")
    threading.Thread(target=_run_in_background, args=(acct.id, body.kind), daemon=True).start()
    return {"ok": True}
