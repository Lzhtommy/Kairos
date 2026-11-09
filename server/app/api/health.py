from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.market import FactorSnapshot, Kline, Quote
from app.providers.factory import get_provider

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "provider": get_provider().name}


@router.get("/health/data")
def health_data(db: Session = Depends(get_db)) -> dict:
    """数据新鲜度自检：采集是否在跑、K 线到哪天、PIT 快照积累了多少。"""
    from app.jobs.collect import in_trading_session

    quotes_ts = db.execute(select(func.max(Quote.ts))).scalar()
    kline_ts = db.execute(
        select(func.max(Kline.ts)).where(Kline.period == "1d")
    ).scalar()
    snapshot_days = db.execute(
        select(func.count(func.distinct(FactorSnapshot.ts)))
    ).scalar()

    now = datetime.now(timezone.utc)
    lag_seconds = None
    if quotes_ts is not None:
        ts = quotes_ts if quotes_ts.tzinfo else quotes_ts.replace(tzinfo=timezone.utc)
        lag_seconds = int((now - ts).total_seconds())
    trading = in_trading_session(now)
    # 盘中落后 30 分钟即视为断供；非交易时段数据静止是正常的
    stale = bool(trading and (lag_seconds is None or lag_seconds > 1800))

    return {
        "status": "stale" if stale else "ok",
        "provider": get_provider().name,
        "inTradingSession": trading,
        "quotesUpdatedAt": quotes_ts.isoformat() if quotes_ts else None,
        "quotesLagSeconds": lag_seconds,
        "latestKlineDay": kline_ts.strftime("%Y-%m-%d") if kline_ts else None,
        "factorSnapshotDays": snapshot_days or 0,
        "warehouse": _warehouse_freshness(db),
    }


def _warehouse_freshness(db: Session) -> dict:
    """数据仓库各表的最新日期，判断日更是否在跑。"""
    from app.models.warehouse import Announcement, Dividend, FinReport, HolderCount, MacroSeries, TopHolder

    def latest(col):  # noqa: ANN001, ANN202
        v = db.execute(select(func.max(col))).scalar()
        return v.isoformat()[:10] if v else None

    return {
        "finNotice": latest(FinReport.notice_date),
        "dividendNotice": latest(Dividend.notice_date),
        "macro": latest(MacroSeries.period),
        "announcement": latest(Announcement.ann_date),
        "holderCount": latest(HolderCount.end_date),
        "topHolders": latest(TopHolder.end_date),
    }
