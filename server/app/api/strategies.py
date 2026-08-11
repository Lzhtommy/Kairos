import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.security import get_current_user
from app.models.strategy import ChatMessage, Strategy, StrategyRun
from app.models.user import User
from app.schemas import ChatHistoryIn, ChatIn, StrategyIn
from app.services import strategy_ai
from app.services.dsl import validate_dsl
from app.services.market import stock_dicts
from app.services.strategy_exec import run_dsl

router = APIRouter(prefix="/strategies", tags=["strategies"])
logger = logging.getLogger("kairos.strategies")


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


def _last_runs(db: Session, strategy_ids: list[int], n: int = 5) -> dict[int, list[StrategyRun]]:
    """每个策略最近 n 次 run（新→旧），一次查询按策略分组。"""
    if not strategy_ids:
        return {}
    out: dict[int, list[StrategyRun]] = {}
    for r in db.execute(
        select(StrategyRun)
        .where(StrategyRun.strategy_id.in_(strategy_ids))
        .order_by(StrategyRun.strategy_id, StrategyRun.run_date.desc())
    ).scalars():
        bucket = out.setdefault(r.strategy_id, [])
        if len(bucket) < n:
            bucket.append(r)
    return out


def _run_summary(r: StrategyRun) -> dict:
    return {
        "date": r.run_date.strftime("%Y-%m-%d"),
        "hitCount": r.hit_count,
        "addedCount": r.added_count,
        "removedCount": r.removed_count,
        "added": r.added or [],
        "removed": r.removed or [],
        "forward": r.forward or {},
    }


def _pooled_forward(runs: list[StrategyRun]) -> dict | None:
    """近几次 run 的前瞻收益按签数加权合并：{"d5": {"n","avg","win"}, …}。"""
    out: dict[str, dict] = {}
    for key in ("d1", "d5", "d10"):
        parts = [
            f for r in runs
            if (f := (r.forward or {}).get(key)) and f.get("n")
        ]
        total = sum(f["n"] for f in parts)
        if not total:
            continue
        out[key] = {
            "n": total,
            "avg": round(sum(f["avg"] * f["n"] for f in parts) / total, 2),
            "win": round(sum(f["win"] * f["n"] for f in parts) / total, 1),
        }
    return out or None


@router.get("")
def list_strategies(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.execute(
        select(Strategy).where(Strategy.user_id == user.id).order_by(Strategy.created_at.desc())
    ).scalars().all()
    runs = _last_runs(db, [s.id for s in rows])
    out = []
    for s in rows:
        item = _public(s)
        latest = runs.get(s.id) or []
        item["lastRun"] = _run_summary(latest[0]) if latest else None
        # 最近 5 次命中数（旧→新），卡片上画迷你趋势
        item["hitTrend"] = [r.hit_count for r in reversed(latest)]
        # 信号前瞻跟踪：近 5 次 run 的样本外收益合并（未成熟时为 null）
        item["signalStats"] = _pooled_forward(latest)
        out.append(item)
    return out


@router.get("/{sid}/runs")
def strategy_runs(
    sid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """最近 10 次盘后运行记录（新→旧）。"""
    _get_owned(db, sid, user)
    rows = db.execute(
        select(StrategyRun)
        .where(StrategyRun.strategy_id == sid)
        .order_by(StrategyRun.run_date.desc())
        .limit(10)
    ).scalars().all()
    return [_run_summary(r) for r in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_strategy(
    body: StrategyIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    # 保存不跑选股（技术条件要全市场算 K 线，会卡住请求）；
    # hit_count 由 /hits 惰性刷新或盘后任务补齐
    dsl = validate_dsl(body.dsl) if body.dsl else {}
    s = Strategy(
        user_id=user.id,
        name=body.name,
        description=body.description,
        tags=body.tags,
        dsl=dsl,
        code=body.code,
        hit_count=0,
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
        s.dsl = validate_dsl(body.dsl)  # hit_count 不同步重算，等 /hits 或盘后任务
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
    matched = run_dsl(db, s.dsl)
    codes = [r["code"] for r in matched]
    # keep hit_count fresh
    if s.hit_count != len(codes):
        s.hit_count = len(codes)
        db.commit()
    return {"total": len(codes), "items": stock_dicts(db, codes)}


@router.get("/{sid}/chat")
def get_chat_history(
    sid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    s = _get_owned(db, sid, user)
    msgs = db.execute(
        select(ChatMessage).where(ChatMessage.strategy_id == s.id).order_by(ChatMessage.id)
    ).scalars()
    return [{"role": m.role, "text": m.text, "code": m.code} for m in msgs]


@router.put("/{sid}/chat")
def put_chat_history(
    sid: int,
    body: ChatHistoryIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """全量替换该策略的对话历史（幂等，前端在保存/每轮对话后同步）。"""
    s = _get_owned(db, sid, user)
    db.execute(delete(ChatMessage).where(ChatMessage.strategy_id == s.id))
    db.add_all(
        ChatMessage(strategy_id=s.id, role=m.role, text=m.text, code=m.code)
        for m in body.messages
    )
    db.commit()
    return {"count": len(body.messages)}


@router.post("/chat")
async def chat(
    body: ChatIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """SSE 多轮对话流。主路径：DeepSeek 真流式（闲聊直接回复，策略请求额外带 dsl+code）；
    DeepSeek 不可用时降级到规则解析的一次性生成 + 分块假流式。"""
    from app.models.market import StockInfo

    industries = [
        r[0]
        for r in db.execute(
            select(StockInfo.industry).where(StockInfo.industry != "—").distinct()
        ).all()
    ]
    history = [t.model_dump() for t in body.history]

    def sse(ev: dict) -> str:
        return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    async def event_stream():
        if settings.ai_provider.lower() in ("auto", "deepseek") and settings.deepseek_api_key:
            emitted = False
            try:
                async for ev in strategy_ai.chat_stream(
                    body.text, history, body.currentDsl, industries or None,
                    last_backtest=body.lastBacktest,
                ):
                    emitted = True
                    yield sse(ev)
                return
            except Exception:  # noqa: BLE001
                logger.exception("DeepSeek chat stream failed, falling back to rule parser")
                if emitted:
                    # 已经吐过内容，补个收尾事件，不能再叠加兜底回复
                    yield sse({"type": "done"})
                    return

        result = strategy_ai.generate(body.text, industries=industries or None)
        chunk = ""
        for ch in result["explanation"]:
            chunk += ch
            if len(chunk) >= 6:
                yield sse({"type": "text", "delta": chunk})
                chunk = ""
                await asyncio.sleep(0.02)
        if chunk:
            yield sse({"type": "text", "delta": chunk})
        yield sse({"type": "done", "dsl": result["dsl"], "code": result["code"]})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
