"""行业轮动 API：RRG / 拥挤度预警 / 倾斜建议 / 叙事。

只读市场数据，与 quotes 一样不鉴权。产品形态（research PLAN Step 5）：
等权基准 ± 有限倾斜 + 过热预警，不做 Top-N 满仓信号。
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.jobs.sector import load_bar_panel
from app.models.sector import SectorInfo, SectorMetric, SectorNarrative, SectorTilt
from app.services.sector_metrics import calc_rrg

router = APIRouter(prefix="/sector", tags=["sector"])

# RRG 需要 26 周均值 + 6 周轨迹 + 缓冲 ≈ 每板块最近 300 根日线
_RRG_TAIL = 300


def _round(v: float | None, n: int = 3) -> float | None:
    return None if v is None else round(v, n)


@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    infos = db.execute(select(SectorInfo)).scalars().all()
    if not infos:
        return {"source": None, "asof": None, "tilt_month": None,
                "market_narrative": None, "sectors": []}
    names = {i.code: i.name for i in infos}

    panel = load_bar_panel(db, tail=_RRG_TAIL)
    rrg = calc_rrg(panel) if panel else {"asof": None, "sectors": []}
    rrg_by_code = {s["code"]: s for s in rrg["sectors"]}

    # 最新收盘与涨跌幅（面板末两根非空）
    px: dict[str, dict] = {}
    if panel:
        for code in panel.codes:
            closes = [(i, c) for i, c in enumerate(panel.closes[code]) if c is not None]
            if not closes:
                continue
            close = closes[-1][1]
            prev = closes[-2][1] if len(closes) >= 2 else None
            px[code] = {
                "close": round(close, 2),
                "chg_pct": round((close / prev - 1) * 100, 2) if prev else None,
            }

    metric_ts = db.execute(select(func.max(SectorMetric.ts))).scalar()
    metrics = {}
    if metric_ts is not None:
        for m in db.execute(
            select(SectorMetric).where(SectorMetric.ts == metric_ts)
        ).scalars():
            metrics[m.code] = {
                "crowd": _round(m.crowd), "share": _round(m.crowd_share),
                "heat": _round(m.crowd_heat), "bias": _round(m.crowd_bias),
            }

    tilt_month = db.execute(select(func.max(SectorTilt.month))).scalar()
    tilts = {}
    if tilt_month is not None:
        for t in db.execute(
            select(SectorTilt).where(SectorTilt.month == tilt_month)
        ).scalars():
            tilts[t.code] = {
                "score": _round(t.score), "rank": t.rank, "suggestion": t.suggestion,
                "rev1_z": _round(t.rev1_z, 2), "season_z": _round(t.season_z, 2),
                "resmom_z": _round(t.resmom_z, 2),
            }

    narr_date = db.execute(select(func.max(SectorNarrative.date))).scalar()
    narratives, market_narr = {}, None
    if narr_date is not None:
        for nr in db.execute(
            select(SectorNarrative).where(SectorNarrative.date == narr_date)
        ).scalars():
            item = {
                "date": nr.date.date().isoformat(),
                "direction": _round(nr.direction, 2), "strength": _round(nr.strength, 2),
                "summary": nr.summary, "evidence": nr.evidence,
            }
            if nr.code == "_market":
                market_narr = item
            else:
                narratives[nr.code] = item

    sectors = []
    for code, name in sorted(names.items()):
        sectors.append(
            {
                "code": code,
                "name": name,
                **px.get(code, {"close": None, "chg_pct": None}),
                "rrg": rrg_by_code.get(code),
                "crowd": metrics.get(code),
                "tilt": tilts.get(code),
                "narrative": narratives.get(code),
            }
        )
    return {
        "source": infos[0].source,
        "asof": rrg["asof"] or (metric_ts.date().isoformat() if metric_ts else None),
        "metric_date": metric_ts.date().isoformat() if metric_ts else None,
        "tilt_month": tilt_month.strftime("%Y-%m") if tilt_month else None,
        "market_narrative": market_narr,
        "sectors": sectors,
    }


@router.get("/narrative")
def narrative_history(days: int = Query(default=14, le=90), db: Session = Depends(get_db)):
    """最近 N 天的叙事记录（前向积累的原始留痕，按日期倒序分组）。"""
    latest = db.execute(select(func.max(SectorNarrative.date))).scalar()
    if latest is None:
        return []
    floor = latest - timedelta(days=days)
    names = {i.code: i.name for i in db.execute(select(SectorInfo)).scalars()}
    rows = db.execute(
        select(SectorNarrative)
        .where(SectorNarrative.date >= floor)
        .order_by(SectorNarrative.date.desc(), SectorNarrative.strength.desc())
    ).scalars().all()
    by_date: dict[str, dict] = {}
    for nr in rows:
        d = nr.date.date().isoformat()
        day = by_date.setdefault(d, {"date": d, "market": None, "items": []})
        item = {
            "code": nr.code, "name": names.get(nr.code, nr.code),
            "direction": _round(nr.direction, 2), "strength": _round(nr.strength, 2),
            "summary": nr.summary, "evidence": nr.evidence,
        }
        if nr.code == "_market":
            day["market"] = item
        else:
            day["items"].append(item)
    return list(by_date.values())
