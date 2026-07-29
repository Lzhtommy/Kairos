import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import get_current_user
from app.models.strategy import Strategy
from app.models.user import User
from app.schemas import ChatIn, StrategyIn
from app.services import strategy_ai
from app.services.dsl import execute, validate_dsl
from app.services.market import factor_rows, stock_dicts

router = APIRouter(prefix="/strategies", tags=["strategies"])


def _public(s: Strategy) -> dict:
    return {
        "id": str(s.id),
        "name": s.name,
        "description": s.description,
        "tags": s.tags or [],
        "dsl": s.dsl or {},
        "code": s.code,
        "hitCount": s.hit_count,
        "createdAt": s.created_at.strftime("%Y-%m-%d"),
    }


def _get_owned(db: Session, sid: int, user: User) -> Strategy:
    s = db.get(Strategy, sid)
    if not s or s.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "策略不存在")
    return s


@router.get("")
def list_strategies(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.execute(
        select(Strategy).where(Strategy.user_id == user.id).order_by(Strategy.created_at.desc())
    ).scalars().all()
    return [_public(s) for s in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_strategy(
    body: StrategyIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    dsl = validate_dsl(body.dsl) if body.dsl else {}
    hit_count = 0
    if dsl:
        hit_count = len(execute(dsl, factor_rows(db)))
    s = Strategy(
        user_id=user.id,
        name=body.name,
        description=body.description,
        tags=body.tags,
        dsl=dsl,
        code=body.code,
        hit_count=hit_count,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return _public(s)


@router.put("/{sid}")
def update_strategy(
    sid: int, body: StrategyIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    s = _get_owned(db, sid, user)
    s.name = body.name
    s.description = body.description
    s.tags = body.tags
    if body.dsl:
        s.dsl = validate_dsl(body.dsl)
        s.hit_count = len(execute(s.dsl, factor_rows(db)))
    s.code = body.code
    db.commit()
    db.refresh(s)
    return _public(s)


@router.delete("/{sid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_strategy(sid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = _get_owned(db, sid, user)
    db.delete(s)
    db.commit()


@router.get("/{sid}/hits")
def strategy_hits(sid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = _get_owned(db, sid, user)
    if not s.dsl:
        return {"total": 0, "items": []}
    matched = execute(s.dsl, factor_rows(db))
    codes = [r["code"] for r in matched]
    # keep hit_count fresh
    if s.hit_count != len(codes):
        s.hit_count = len(codes)
        db.commit()
    return {"total": len(codes), "items": stock_dicts(db, codes)}


@router.post("/chat")
async def chat(
    body: ChatIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """SSE stream: incremental explanation, then a final payload with dsl + code."""
    from app.models.market import StockInfo

    industries = [
        r[0]
        for r in db.execute(
            select(StockInfo.industry).where(StockInfo.industry != "—").distinct()
        ).all()
    ]
    result = strategy_ai.generate(body.text, industries=industries or None)

    async def event_stream():
        explanation = result["explanation"]
        # stream the explanation in small chunks
        chunk = ""
        for ch in explanation:
            chunk += ch
            if len(chunk) >= 6:
                yield f"data: {json.dumps({'type': 'text', 'delta': chunk}, ensure_ascii=False)}\n\n"
                chunk = ""
                await asyncio.sleep(0.02)
        if chunk:
            yield f"data: {json.dumps({'type': 'text', 'delta': chunk}, ensure_ascii=False)}\n\n"
        final = {"type": "done", "dsl": result["dsl"], "code": result["code"]}
        yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
