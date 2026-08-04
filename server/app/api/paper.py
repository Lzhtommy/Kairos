"""模拟盘账户 API：按策略开关、查询净值曲线 / 持仓 / 交易流水。"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import get_current_user
from app.models.strategy import (
    PaperAccount,
    PaperEquity,
    PaperPosition,
    PaperTrade,
    Strategy,
)
from app.models.user import User
from app.schemas import PaperToggleIn
from app.services.paper import clean_params

router = APIRouter(prefix="/paper", tags=["paper"])


def _owned_strategy(db: Session, sid: int, user: User) -> Strategy:
    s = db.get(Strategy, sid)
    if not s or s.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "策略不存在")
    return s


def _account_public(db: Session, acct: PaperAccount, name: str) -> dict:
    positions = db.execute(
        select(PaperPosition).where(PaperPosition.account_id == acct.id)
        .order_by(PaperPosition.status.desc(), PaperPosition.code)
    ).scalars().all()
    trades = db.execute(
        select(PaperTrade).where(PaperTrade.account_id == acct.id)
        .order_by(PaperTrade.exit_date.desc(), PaperTrade.id.desc()).limit(100)
    ).scalars().all()
    curve = db.execute(
        select(PaperEquity).where(PaperEquity.account_id == acct.id)
        .order_by(PaperEquity.date.asc())
    ).scalars().all()
    closed_rets = [t.ret for t in trades]
    return {
        "strategyId": acct.strategy_id,
        "strategyName": name,
        "enabled": acct.enabled,
        "params": clean_params(acct.params),
        "equity": acct.equity,
        "stats": acct.stats or {},
        "startedAt": curve[0].date.strftime("%Y-%m-%d") if curve else None,
        "curve": [
            {"t": e.date.strftime("%Y-%m-%d"), "v": round(e.equity, 4)} for e in curve
        ],
        "positions": [
            {
                "code": x.code, "name": x.name, "status": x.status,
                "signalDate": x.signal_date.strftime("%Y-%m-%d"),
                "entryDate": x.entry_date.strftime("%Y-%m-%d") if x.entry_date else None,
                "entryPx": x.entry_px, "lastPrice": x.last_price,
                "holdDays": x.hold_days,
                "ret": (
                    round((x.last_price / x.entry_px - 1) * 100, 2)
                    if x.status == "open" and x.entry_px
                    else None
                ),
            }
            for x in positions
        ],
        "trades": [
            {
                "code": t.code, "name": t.name,
                "entryDate": t.entry_date.strftime("%Y-%m-%d"), "entryPx": t.entry_px,
                "exitDate": t.exit_date.strftime("%Y-%m-%d"), "exitPx": t.exit_px,
                "ret": t.ret, "reason": t.reason,
            }
            for t in trades
        ],
        "tradeCount": len(trades),
        "winRate": (
            round(100 * sum(r > 0 for r in closed_rets) / len(closed_rets), 1)
            if closed_rets
            else None
        ),
    }


@router.get("")
def list_accounts(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """当前用户全部模拟盘账户概要（含未开启过模拟盘的策略，供开关列表用）。"""
    strategies = db.execute(
        select(Strategy).where(Strategy.user_id == user.id).order_by(Strategy.created_at.desc())
    ).scalars().all()
    accounts = {
        a.strategy_id: a
        for a in db.execute(
            select(PaperAccount).where(PaperAccount.user_id == user.id)
        ).scalars()
    }
    out = []
    for s in strategies:
        a = accounts.get(s.id)
        out.append({
            "strategyId": s.id,
            "strategyName": s.name,
            "enabled": bool(a and a.enabled),
            "equity": a.equity if a else None,
            "lastSettled": a.last_settled.strftime("%Y-%m-%d") if a and a.last_settled else None,
        })
    return out


@router.get("/{sid}")
def get_account(sid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = _owned_strategy(db, sid, user)
    acct = db.execute(
        select(PaperAccount).where(PaperAccount.strategy_id == sid)
    ).scalar()
    if acct is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "该策略未开启模拟盘")
    return _account_public(db, acct, s.name)


@router.post("/{sid}", status_code=status.HTTP_200_OK)
def toggle_account(
    sid: int,
    body: PaperToggleIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """开启/关闭模拟跟单；首次开启创建账户（当日收信号，次日开盘首买）。"""
    s = _owned_strategy(db, sid, user)
    if body.enabled and not s.dsl:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "策略缺少 DSL，无法模拟")
    acct = db.execute(
        select(PaperAccount).where(PaperAccount.strategy_id == sid)
    ).scalar()
    if acct is None:
        if not body.enabled:
            return {"enabled": False}
        acct = PaperAccount(
            strategy_id=sid, user_id=user.id, enabled=True,
            params=clean_params(body.params), equity=1.0,
        )
        db.add(acct)
    else:
        acct.enabled = body.enabled
        if body.params is not None:
            acct.params = clean_params(body.params)
    db.commit()
    db.refresh(acct)
    return {"enabled": acct.enabled, "params": clean_params(acct.params)}


@router.delete("/{sid}", status_code=status.HTTP_204_NO_CONTENT)
def reset_account(sid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """删除账户与全部历史（重置模拟盘）。"""
    _owned_strategy(db, sid, user)
    acct = db.execute(
        select(PaperAccount).where(PaperAccount.strategy_id == sid)
    ).scalar()
    if acct:
        db.delete(acct)
        db.commit()
