"""数据查阅：个股资料（基本信息、财报、分红、指数成分、公告）与宏观利率序列。"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from kairos_data.store import upsert_rows
from app.core.security import get_current_user
from kairos_data.sync import MACRO, announcement_rows
from app.models.market import Fundamental, Kline, Quote, StockInfo
from app.models.warehouse import Announcement, Dividend, FinReport, IndexConstituent, MacroSeries
from kairos_data.sources import eastmoney_dc as eastmoney_data
from app.services.market import change_pct

logger = logging.getLogger("kairos.data")

router = APIRouter(prefix="/data", tags=["data"], dependencies=[Depends(get_current_user)])

INDEX_NAMES = {"000016": "上证 50", "000300": "沪深 300", "000905": "中证 500"}


def _iso(d: date | datetime | None) -> str | None:
    return d.isoformat()[:10] if d else None


@router.get("/search")
def search(q: str = Query(min_length=1, max_length=32), limit: int = Query(default=20, le=50),
           db: Session = Depends(get_db)):
    q = q.strip()
    rows = db.execute(
        select(StockInfo)
        .where(or_(StockInfo.code.startswith(q), StockInfo.name.contains(q)))
        # 在市的排前面，再按代码
        .order_by(StockInfo.delist_date.is_not(None), StockInfo.code)
        .limit(limit)
    ).scalars()
    return [
        {"code": s.code, "name": s.name, "market": s.market, "industry": s.industry,
         "delistDate": _iso(s.delist_date)}
        for s in rows
    ]


def _index_membership(db: Session, code: str) -> list[dict]:
    snaps: dict[str, list[date]] = {}
    for idx, snap in db.execute(
        select(IndexConstituent.index_code, IndexConstituent.snapshot_date).distinct()
    ).tuples():
        snaps.setdefault(idx, []).append(snap)
    mine: dict[str, list[date]] = {}
    for idx, snap in db.execute(
        select(IndexConstituent.index_code, IndexConstituent.snapshot_date)
        .where(IndexConstituent.code == code)
    ).tuples():
        mine.setdefault(idx, []).append(snap)
    out = []
    for idx, dates in sorted(mine.items()):
        out.append({
            "index": idx, "name": INDEX_NAMES.get(idx, idx),
            "current": max(dates) == max(snaps[idx]),
            "first": _iso(min(dates)), "last": _iso(max(dates)),
        })
    return out


@router.get("/stock/{code}")
def stock_profile(code: str, db: Session = Depends(get_db)):
    info = db.get(StockInfo, code)
    if info is None:
        raise HTTPException(status_code=404, detail="没有这只股票")
    q = db.get(Quote, code)
    fund = db.get(Fundamental, code)
    first, last, n = db.execute(
        select(func.min(Kline.ts), func.max(Kline.ts), func.count())
        .where(Kline.code == code, Kline.period == "1d")
    ).one()
    fins = db.execute(
        select(FinReport).where(FinReport.code == code).order_by(FinReport.report_date.desc()).limit(40)
    ).scalars().all()
    divs = db.execute(
        select(Dividend).where(Dividend.code == code).order_by(Dividend.report_date.desc())
    ).scalars().all()
    return {
        "info": {
            "code": info.code, "name": info.name, "market": info.market, "industry": info.industry,
            "listDate": _iso(info.list_date), "delistDate": _iso(info.delist_date),
        },
        "quote": None if q is None or info.delist_date else {
            "price": q.price, "changePct": change_pct(q.price, q.prev_close), "pe": q.pe, "pb": q.pb,
            "marketCap": q.market_cap, "turnoverRate": q.turnover_rate,
            "dividendYield": fund.dividend_yield if fund else None,
            "ts": q.ts.isoformat() if q.ts else None,
        },
        "kline": {"first": _iso(first), "last": _iso(last), "count": n},
        "indexes": _index_membership(db, code),
        "financials": [
            {"reportDate": _iso(f.report_date), "noticeDate": _iso(f.notice_date), "eps": f.eps,
             "deductEps": f.deduct_eps, "revenue": f.revenue, "netProfit": f.net_profit,
             "revenueYoy": f.revenue_yoy, "profitYoy": f.profit_yoy, "roe": f.roe, "bps": f.bps,
             "ocfPs": f.ocf_ps, "grossMargin": f.gross_margin, "dividendPlan": f.dividend_plan}
            for f in fins
        ],
        "dividends": [
            {"reportDate": _iso(d.report_date), "planNoticeDate": _iso(d.plan_notice_date),
             "recordDate": _iso(d.record_date), "exDate": _iso(d.ex_date), "cashPer10": d.cash_per10,
             "bonusPer10": d.bonus_per10, "transferPer10": d.transfer_per10,
             "progress": d.progress, "plan": d.plan}
            for d in divs
        ],
    }


def _ann_dict(a: Announcement) -> dict:
    return {"artCode": a.art_code, "title": a.title, "category": a.category,
            "date": _iso(a.ann_date), "summary": a.summary}


@router.get("/stock/{code}/announcements")
def stock_announcements(code: str, page: int = Query(default=1, ge=1, le=200),
                        db: Session = Depends(get_db)):
    """先从东财拉这一页并落库（历史公告按需补齐），接口失败时退回库里已有的。"""
    size = 30
    total = None
    try:
        items, total = eastmoney_data.fetch_announcements(page, size, code=code)
        rows = announcement_rows(items, code=code)
        upsert_rows(db, Announcement, rows)
        db.commit()
        arts = [r["art_code"] for r in rows]
        by_art = {a.art_code: a for a in db.execute(
            select(Announcement).where(Announcement.art_code.in_(arts))).scalars()} if arts else {}
        out = [_ann_dict(by_art[a]) for a in arts if a in by_art]
    except Exception:  # noqa: BLE001
        logger.warning("announcement fetch failed for %s, serving cached", code, exc_info=True)
        db.rollback()
        out = [_ann_dict(a) for a in db.execute(
            select(Announcement).where(Announcement.code == code)
            .order_by(Announcement.ann_date.desc()).offset((page - 1) * size).limit(size)
        ).scalars()]
    return {"page": page, "pageSize": size, "total": total, "items": out}


_SUMMARY_PROMPT = (
    "你是 A 股公告解读助手。阅读下面的上市公司公告，输出 JSON："
    '{"summary": "一两句话概括公告讲了什么", "points": ["要点1", "要点2", ...], '
    '"impact": "利好 / 利空 / 中性 / 不确定 之一", "reason": "一句话说明判断依据"}。'
    "要点 2~5 条，只写公告里明确出现的事实和数字，不要编造，不要投资建议。\n\n"
)


@router.post("/announcements/{art_code}/summary")
def announcement_summary(art_code: str, db: Session = Depends(get_db)):
    from app.services.research_writer import _chat_json, llm_enabled

    ann = db.get(Announcement, art_code)
    if ann is None:
        raise HTTPException(status_code=404, detail="公告不存在")
    if ann.summary:
        return _ann_dict(ann)
    if not llm_enabled():
        raise HTTPException(status_code=503, detail="AI 未配置")
    try:
        text, pdf = eastmoney_data.fetch_announcement_text(art_code)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="公告正文获取失败") from exc
    if len(text) < 30:
        raise HTTPException(status_code=422, detail="公告没有可解析的正文（可能是扫描件）")
    try:
        out, _tokens = _chat_json([{"role": "user", "content": _SUMMARY_PROMPT
                                    + f"标题：{ann.title}\n\n正文：\n{text[:12000]}"}], max_tokens=800)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="AI 摘要生成失败") from exc
    lines = [str(out.get("summary") or "").strip()]
    lines += [f"- {p}" for p in out.get("points") or [] if str(p).strip()]
    if out.get("impact"):
        lines.append(f"\n**影响判断：{out['impact']}**" + (f"（{out['reason']}）" if out.get("reason") else ""))
    if pdf:
        lines.append(f"\n[公告原文 PDF]({pdf})")
    ann.summary = "\n".join(lines).strip()
    ann.summary_at = datetime.now(timezone.utc)
    db.commit()
    return _ann_dict(ann)


@router.get("/macro")
def macro_catalog(db: Session = Depends(get_db)):
    """每个指标的最新值和上一期值。"""
    out = []
    for key, (name, unit, freq, *_rest) in MACRO.items():
        last2 = db.execute(
            select(MacroSeries.period, MacroSeries.value).where(MacroSeries.indicator == key)
            .order_by(MacroSeries.period.desc()).limit(2)
        ).all()
        out.append({
            "key": key, "name": name, "unit": unit, "freq": freq,
            "period": _iso(last2[0][0]) if last2 else None,
            "value": last2[0][1] if last2 else None,
            "prev": last2[1][1] if len(last2) > 1 else None,
        })
    return out


@router.get("/macro/{indicator}")
def macro_series(indicator: str, years: int = Query(default=10, ge=1, le=40),
                 db: Session = Depends(get_db)):
    if indicator not in MACRO:
        raise HTTPException(status_code=404, detail="未知指标")
    since = date.today() - timedelta(days=365 * years)
    rows = db.execute(
        select(MacroSeries.period, MacroSeries.value)
        .where(MacroSeries.indicator == indicator, MacroSeries.period >= since)
        .order_by(MacroSeries.period)
    ).all()
    name, unit, freq, *_rest = MACRO[indicator]
    return {"key": indicator, "name": name, "unit": unit, "freq": freq,
            "points": [[_iso(p), v] for p, v in rows]}
