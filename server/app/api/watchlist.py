from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.watchlist import Watchlist, WatchlistItem
from app.services.market import stock_dicts

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


def _default_watchlist(db: Session, user: User) -> Watchlist:
    wl = db.execute(
        select(Watchlist).where(Watchlist.user_id == user.id).order_by(Watchlist.id.asc())
    ).scalars().first()
    if wl is None:
        wl = Watchlist(user_id=user.id, name="默认自选", is_default=True)
        db.add(wl)
        db.commit()
        db.refresh(wl)
    return wl


@router.get("")
def get_watchlist(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    wl = _default_watchlist(db, user)
    items = db.execute(
        select(WatchlistItem).where(WatchlistItem.watchlist_id == wl.id)
    ).scalars().all()
    codes = [i.stock_code for i in items]
    return {"codes": codes, "items": stock_dicts(db, codes) if codes else []}


@router.post("/{code}", status_code=status.HTTP_201_CREATED)
def add_item(code: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    wl = _default_watchlist(db, user)
    exists = db.execute(
        select(WatchlistItem).where(
            WatchlistItem.watchlist_id == wl.id, WatchlistItem.stock_code == code
        )
    ).scalar_one_or_none()
    if not exists:
        db.add(WatchlistItem(watchlist_id=wl.id, stock_code=code))
        db.commit()
    return {"ok": True, "code": code}


@router.delete("/{code}", status_code=status.HTTP_204_NO_CONTENT)
def remove_item(code: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    wl = _default_watchlist(db, user)
    item = db.execute(
        select(WatchlistItem).where(
            WatchlistItem.watchlist_id == wl.id, WatchlistItem.stock_code == code
        )
    ).scalar_one_or_none()
    if item:
        db.delete(item)
        db.commit()
