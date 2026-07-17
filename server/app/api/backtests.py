from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, get_db
from app.core.security import get_current_user
from app.models.strategy import Backtest, Strategy
from app.models.user import User
from app.schemas import BacktestIn
from app.services import backtest as bt

router = APIRouter(prefix="/backtests", tags=["backtests"])


def _run_backtest(backtest_id: int, dsl: dict, params: dict) -> None:
    db = SessionLocal()
    try:
        rec = db.get(Backtest, backtest_id)
        if rec is None:
            return
        rec.status = "running"
        db.commit()
        try:
            result = bt.run(db, dsl, params)
            curve_ref = bt.write_curve(backtest_id, result["curve"])
            rec.metrics = result["metrics"]
            rec.curve_ref = curve_ref
            rec.status = "done"
        except Exception as exc:  # noqa: BLE001
            rec.status = "failed"
            rec.error = str(exc)
        db.commit()
    finally:
        db.close()


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def create_backtest(
    body: BacktestIn,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = db.get(Strategy, body.strategyId)
    if not s or s.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "策略不存在")
    if not s.dsl:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "策略缺少 DSL，无法回测")

    params = {"start": body.start, "rebalance": body.rebalance, "cost": {"rate": body.costRate}}
    rec = Backtest(strategy_id=s.id, params=params, status="pending", metrics={})
    db.add(rec)
    db.commit()
    db.refresh(rec)
    background.add_task(_run_backtest, rec.id, dict(s.dsl), params)
    return {"id": rec.id, "status": rec.status}


@router.get("/{bid}")
def get_backtest(bid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rec = db.get(Backtest, bid)
    if not rec:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "回测不存在")
    s = db.get(Strategy, rec.strategy_id)
    if not s or s.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "回测不存在")
    curve = []
    if rec.curve_ref:
        import json
        import os

        if os.path.exists(rec.curve_ref):
            with open(rec.curve_ref, encoding="utf-8") as fh:
                curve = json.load(fh)
    return {
        "id": rec.id,
        "status": rec.status,
        "metrics": rec.metrics,
        "curve": curve,
        "error": rec.error,
    }
