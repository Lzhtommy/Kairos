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
            curve_ref = bt.write_curve(
                backtest_id,
                {
                    "curve": result["curve"],
                    "benchmark": result["benchmark"],
                    "trades": result.get("trades", []),
                    "rebalanceRecords": result.get("rebalanceRecords", []),
                },
            )
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

    params = body.model_dump(exclude={"strategyId"})
    rec = Backtest(strategy_id=s.id, params=params, status="pending", metrics={})
    db.add(rec)
    db.commit()
    db.refresh(rec)
    background.add_task(_run_backtest, rec.id, dict(s.dsl), params)
    return {"id": rec.id, "status": rec.status}


def _load_payload(rec: Backtest) -> dict:
    import json
    import os

    if not rec.curve_ref or not os.path.exists(rec.curve_ref):
        return {}
    with open(rec.curve_ref, encoding="utf-8") as fh:
        payload = json.load(fh)
    if isinstance(payload, list):  # 旧格式：裸曲线数组
        return {"curve": payload}
    return payload


def _get_owned_backtest(db: Session, bid: int, user: User) -> Backtest:
    rec = db.get(Backtest, bid)
    if not rec:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "回测不存在")
    s = db.get(Strategy, rec.strategy_id)
    if not s or s.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "回测不存在")
    return rec


@router.get("/{bid}/trades")
def backtest_trades(
    bid: int,
    page: int = 1,
    pageSize: int = 50,
    sort: str = "date",  # date | ret
    order: str = "desc",  # asc | desc
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """逐笔交易明细（事件模式）或调仓记录（组合模式），分页 + 排序。"""
    rec = _get_owned_backtest(db, bid, user)
    payload = _load_payload(rec)
    page = max(page, 1)
    page_size = min(max(pageSize, 1), 200)
    reverse = order != "asc"

    trades = payload.get("trades") or []
    if trades:
        key = "ret" if sort == "ret" else "entryDate"
        trades = sorted(trades, key=lambda t: t.get(key) or 0, reverse=reverse)
        start = (page - 1) * page_size
        return {
            "kind": "trades",
            "total": len(trades),
            "page": page,
            "pageSize": page_size,
            "items": trades[start : start + page_size],
        }

    records = payload.get("rebalanceRecords") or []
    records = sorted(records, key=lambda r: r.get("date", ""), reverse=reverse)
    start = (page - 1) * page_size
    return {
        "kind": "rebalances",
        "total": len(records),
        "page": page,
        "pageSize": page_size,
        "items": records[start : start + page_size],
    }


@router.get("/{bid}")
def get_backtest(bid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rec = _get_owned_backtest(db, bid, user)
    payload = _load_payload(rec)
    return {
        "id": rec.id,
        "status": rec.status,
        "metrics": rec.metrics,
        "curve": payload.get("curve", []),
        "benchmark": payload.get("benchmark", []),
        "error": rec.error,
    }
