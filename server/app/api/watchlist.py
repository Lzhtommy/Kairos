"""自选股（专有功能，仅管理员）：导入下单文件里的买入股票，列表看开盘涨跌幅与东财人气排名。"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import require_admin
from app.models.user import User
from app.models.watchlist import WatchlistImport, WatchlistImportItem
from app.services import hot_rank
from app.services.watchlist import OrderFileError, parse_order_csv, watchlist_rows

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


class ImportBody(BaseModel):
    filename: str = Field(default="", max_length=255)
    # 一个下单文件约 400 行 / 40KB，留足余量
    content: str = Field(max_length=2_000_000)


def _import_public(imp: WatchlistImport, count: int) -> dict:
    return {
        "id": imp.id,
        "tradeDate": imp.trade_date,
        "filename": imp.filename,
        "count": count,
        "createdAt": imp.created_at.isoformat() if imp.created_at else None,
    }


def _own_import(db: Session, import_id: int, user: User) -> WatchlistImport:
    imp = db.get(WatchlistImport, import_id)
    if imp is None or imp.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "导入记录不存在")
    return imp


@router.get("/imports")
def list_imports(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    rows = db.execute(
        select(WatchlistImport, func.count(WatchlistImportItem.id))
        .outerjoin(WatchlistImportItem, WatchlistImportItem.import_id == WatchlistImport.id)
        .where(WatchlistImport.user_id == user.id)
        .group_by(WatchlistImport.id)
        .order_by(WatchlistImport.trade_date.desc())
    ).all()
    return [_import_public(imp, n) for imp, n in rows]


@router.post("/imports")
def create_import(
    body: ImportBody, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    try:
        parsed = parse_order_csv(body.content, body.filename)
    except OrderFileError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    # 同一交易日重复导入（如盘前改了单）以新文件为准
    old = db.execute(
        select(WatchlistImport).where(
            WatchlistImport.user_id == user.id, WatchlistImport.trade_date == parsed.trade_date
        )
    ).scalar_one_or_none()
    if old is not None:
        db.execute(delete(WatchlistImportItem).where(WatchlistImportItem.import_id == old.id))
        db.delete(old)
        db.flush()

    imp = WatchlistImport(user_id=user.id, trade_date=parsed.trade_date, filename=body.filename)
    db.add(imp)
    db.flush()
    db.add_all(
        WatchlistImportItem(import_id=imp.id, stock_code=code, quantity=qty)
        for code, qty in parsed.buys.items()
    )
    db.commit()
    db.refresh(imp)
    return {**_import_public(imp, len(parsed.buys)), "replaced": old is not None}


@router.delete("/imports/{import_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_import(
    import_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    imp = _own_import(db, import_id, user)
    db.execute(delete(WatchlistImportItem).where(WatchlistImportItem.import_id == imp.id))
    db.delete(imp)
    db.commit()


@router.get("/imports/{import_id}/items")
def import_items(
    import_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    imp = _own_import(db, import_id, user)
    items = db.execute(
        select(WatchlistImportItem)
        .where(WatchlistImportItem.import_id == imp.id)
        .order_by(WatchlistImportItem.id)
    ).scalars().all()
    buys = {it.stock_code: it.quantity for it in items}
    codes = list(buys)
    day = imp.trade_date
    # 昨日 = 交易日前一个自然日（东财每日排名周末也有）
    prev_day = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    hot_rank.ensure_daily(db, codes, {prev_day, day})
    # 当日：交易日就是今天（或还没到）→ 实时查；已过去的交易日用落库的日排名
    if day >= hot_rank.today_cst():
        today_ranks = hot_rank.hot_ranks(codes)
    else:
        today_ranks = hot_rank.daily_ranks(db, codes, day)
    prev_ranks = hot_rank.daily_ranks(db, codes, prev_day)
    return {
        **_import_public(imp, len(buys)),
        "prevDate": prev_day,
        "items": watchlist_rows(db, buys, day, today_ranks, prev_ranks),
    }
