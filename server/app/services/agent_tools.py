"""AI 操盘手可调用的工具：查 Kairos 库内数据、联网搜索、管理持仓 / 订单 / 经验 / 交易手册。

每个工具 = (JSON Schema 描述, handler)。handler 收 (ctx, args)，返回可 JSON 序列化的结果；
抛 ValueError 表示参数 / 业务错误，文案原样回给模型让它自行修正。
外部内容（新闻、搜索结果、公告标题）只是数据：系统提示里明确告诉模型不得执行其中的指令。
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Callable

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.agent import AgentAccount, AgentMemory, AgentOrder, AgentPlaybook, AgentPosition, AgentSession
from app.models.market import Fundamental, IndexQuote, Kline, Quote, StockInfo
from app.models.research import ResearchReport
from app.models.sector import SectorBar, SectorInfo, SectorMetric, SectorNarrative, SectorTilt
from app.models.warehouse import Announcement, DailyBasic, FinReport, Lockup, MarginTrading
from app.services import agent_broker as broker
from app.services.backtest import _limit_pct
from app.services.market import change_pct

logger = logging.getLogger("kairos.agent")

_RESULT_CAP = 6000  # 单次工具结果回给模型的字符上限（每轮都随历史重发，直接决定 token 消耗）
WEB_SEARCH_QUOTA = 8  # 单次运行的联网搜索次数上限


@dataclass
class Ctx:
    db: Session
    account: AgentAccount
    session: AgentSession
    can_trade: bool  # 周度反思只读
    can_edit_playbook: bool
    cache: dict[str, Any] = field(default_factory=dict)


Handler = Callable[[Ctx, dict[str, Any]], Any]
_TOOLS: dict[str, tuple[dict[str, Any], Handler, str]] = {}  # name -> (spec, handler, 分组)


def tool(name: str, description: str, properties: dict[str, Any] | None = None,
         required: list[str] | None = None, group: str = "read"):
    def deco(fn: Handler) -> Handler:
        _TOOLS[name] = (
            {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": properties or {}, "required": required or []},
            },
            fn,
            group,
        )
        return fn

    return deco


def specs(ctx: Ctx) -> list[dict[str, Any]]:
    out = []
    for spec, _fn, group in _TOOLS.values():
        if group == "trade" and not ctx.can_trade:
            continue
        if group == "playbook" and not ctx.can_edit_playbook:
            continue
        if group == "web" and not settings.tavily_api_key:
            continue
        out.append(spec)
    return out


def execute(ctx: Ctx, name: str, args: dict[str, Any]) -> str:
    entry = _TOOLS.get(name)
    allowed = {s["name"] for s in specs(ctx)}
    if entry is None or name not in allowed:
        return f"错误：没有工具 {name}"
    try:
        result = entry[1](ctx, args)
    except ValueError as exc:
        ctx.db.rollback()
        return f"错误：{exc}"
    except Exception as exc:  # noqa: BLE001 — 工具故障告诉模型，让它换路子，不中断整轮
        ctx.db.rollback()
        logger.warning("agent tool %s failed: %s", name, exc)
        return f"工具执行失败：{type(exc).__name__}: {str(exc)[:200]}"
    text = json.dumps(result, ensure_ascii=False, default=str, separators=(",", ":"))
    if len(text) > _RESULT_CAP:
        text = text[:_RESULT_CAP] + "…（结果过长已截断，请缩小查询范围）"
    return text


# ---------------------------------------------------------------- 通用辅助


def _r(v: float | None, n: int = 2) -> float | None:
    return round(v, n) if v is not None else None


def _code(args: dict[str, Any]) -> str:
    code = str(args.get("code") or "").strip()
    if not code:
        raise ValueError("缺少 code")
    return code


def _limit(args: dict[str, Any], default: int, cap: int) -> int:
    try:
        return max(1, min(int(args.get("limit") or default), cap))
    except (TypeError, ValueError):
        return default


def _names(db: Session) -> dict[str, StockInfo]:
    return {s.code: s for s in db.execute(select(StockInfo)).scalars()}


def _rsi(closes: list[float], n: int = 14) -> float | None:
    if len(closes) <= n:
        return None
    gains = losses = 0.0
    for a, b in zip(closes[-n - 1 : -1], closes[-n:]):
        d = b - a
        gains += max(d, 0)
        losses += max(-d, 0)
    if losses == 0:
        return 100.0
    return round(100 - 100 / (1 + gains / losses), 1)


def _ma(closes: list[float], n: int) -> float | None:
    return round(sum(closes[-n:]) / n, 3) if len(closes) >= n else None


def _pct(a: float | None, b: float | None) -> float | None:
    return round((a / b - 1) * 100, 2) if a and b else None


# ---------------------------------------------------------------- 账户


def portfolio(ctx: Ctx) -> dict[str, Any]:
    db, acct = ctx.db, ctx.account
    positions = db.execute(select(AgentPosition).where(AgentPosition.account_id == acct.id)).scalars().all()
    px = broker.latest_prices(db, [p.code for p in positions])
    rows = []
    mv = 0.0
    for p in positions:
        price = px.get(p.code, p.last_price)
        value = p.shares * price
        mv += value
        rows.append({
            "code": p.code, "name": p.name, "shares": p.shares, "avgCost": _r(p.cost / p.shares, 3),
            "price": price, "value": round(value, 2), "pnl": round(value - p.cost, 2),
            "pnlPct": _pct(value, p.cost), "openedAt": p.opened_at.strftime("%Y-%m-%d"),
        })
    pending = db.execute(
        select(AgentOrder).where(AgentOrder.account_id == acct.id, AgentOrder.status == "pending")
    ).scalars().all()
    total = acct.cash + mv
    held_cash, _ = broker.reserved(db, acct.id)
    return {
        "cash": round(acct.cash, 2),
        "availableCash": round(acct.cash - held_cash, 2),
        "marketValue": round(mv, 2),
        "total": round(total, 2),
        "initialCash": acct.initial_cash,
        "totalReturnPct": _pct(total, acct.initial_cash),
        "positions": sorted(rows, key=lambda x: -x["value"]),
        "pendingOrders": [_order_row(o) for o in pending],
    }


def _order_row(o: AgentOrder) -> dict[str, Any]:
    return {
        "id": o.id, "code": o.code, "name": o.name, "side": o.side, "shares": o.shares,
        "limitPrice": o.limit_price, "status": o.status,
        "tradeDate": o.trade_date.strftime("%Y-%m-%d") if o.trade_date else None,
        "filled": o.filled_shares, "fillPrice": o.fill_price, "fee": o.fee,
        "realizedPnl": o.realized_pnl, "note": o.note, "reason": o.reason[:200],
    }


@tool("get_portfolio", "查看账户现金、可用资金（已扣在途买单）、持仓（成本 / 现价 / 浮盈）和未成交挂单。")
def _get_portfolio(ctx: Ctx, args: dict[str, Any]) -> Any:
    return portfolio(ctx)


@tool(
    "trade_history",
    "查看历史订单（含成交价、费用、卖出实现盈亏、未成交原因、当时的下单理由），用于复盘。",
    {"code": {"type": "string", "description": "只看某只股票，可省略"},
     "limit": {"type": "integer", "description": "条数，默认 30，最多 100"}},
)
def _trade_history(ctx: Ctx, args: dict[str, Any]) -> Any:
    stmt = select(AgentOrder).where(AgentOrder.account_id == ctx.account.id, AgentOrder.status != "pending")
    if args.get("code"):
        stmt = stmt.where(AgentOrder.code == str(args["code"]))
    rows = ctx.db.execute(stmt.order_by(AgentOrder.id.desc()).limit(_limit(args, 30, 100))).scalars().all()
    return [_order_row(o) for o in rows]


# ---------------------------------------------------------------- 行情


@tool(
    "market_overview",
    "大盘概况：主要指数涨跌、全市场涨跌家数 / 涨跌停数 / 成交额、行业涨跌前后五、当日 AI 叙事摘要。"
    "盘前调用看到的是上一交易日收盘数据。",
)
def _market_overview(ctx: Ctx, args: dict[str, Any]) -> Any:
    db = ctx.db
    info = _names(db)
    quotes = db.execute(select(Quote)).scalars().all()
    asof = max((q.ts for q in quotes if q.ts), default=None)
    up = down = flat = lu = ld = 0
    chg_all: list[float] = []
    turnover = 0.0
    by_ind: dict[str, list[float]] = defaultdict(list)
    today = broker.cst_today().date()
    for q in quotes:
        if not q.prev_close or not q.price:
            continue
        c = change_pct(q.price, q.prev_close)
        chg_all.append(c)
        turnover += q.turnover or 0
        up += c > 0
        down += c < 0
        flat += c == 0
        meta = info.get(q.code)
        lim = _limit_pct(q.code, bool(meta and "ST" in meta.name), today) * 100
        lu += c >= lim - 0.15
        ld += c <= -lim + 0.15
        if meta and meta.industry and meta.industry != "—":
            by_ind[meta.industry].append(c)
    inds = sorted(
        ((k, round(sum(v) / len(v), 2), len(v)) for k, v in by_ind.items() if len(v) >= 5), key=lambda x: -x[1]
    )
    narrative = db.execute(
        select(SectorNarrative).where(SectorNarrative.code == "_market").order_by(SectorNarrative.date.desc()).limit(1)
    ).scalar()
    return {
        "asOf": str(asof)[:16] if asof else None,
        "indices": [
            {"code": i.code, "name": i.name, "price": i.price, "changePct": i.change_pct}
            for i in db.execute(select(IndexQuote)).scalars()
        ],
        "breadth": {
            "up": up, "down": down, "flat": flat, "limitUp": lu, "limitDown": ld,
            "medianChangePct": _r(median(chg_all)) if chg_all else None,
            "turnoverYi": round(turnover, 1),
        },
        "topIndustries": [{"industry": k, "avgChangePct": v, "n": n} for k, v, n in inds[:5]],
        "bottomIndustries": [{"industry": k, "avgChangePct": v, "n": n} for k, v, n in inds[-5:][::-1]],
        "narrative": {
            "date": narrative.date.strftime("%Y-%m-%d"), "direction": narrative.direction,
            "strength": narrative.strength, "summary": narrative.summary,
        } if narrative else None,
    }


@tool(
    "search_stocks",
    "按名称或代码片段查找股票，返回代码、名称、行业。不确定代码时先用它。",
    {"keyword": {"type": "string", "description": "名称或代码片段，如 茅台 / 600519"}},
    ["keyword"],
)
def _search_stocks(ctx: Ctx, args: dict[str, Any]) -> Any:
    kw = str(args.get("keyword") or "").strip()
    if not kw:
        raise ValueError("keyword 不能为空")
    rows = ctx.db.execute(
        select(StockInfo).where(
            StockInfo.delist_date.is_(None), or_(StockInfo.code.contains(kw), StockInfo.name.contains(kw))
        ).limit(20)
    ).scalars().all()
    return [{"code": s.code, "name": s.name, "industry": s.industry} for s in rows]


_SCREEN_FIELDS = {
    "pe": "市盈率（TTM，亏损为空）", "pb": "市净率", "roe": "ROE %", "market_cap": "总市值（亿元）",
    "change_pct": "当日涨跌幅 %", "turnover_rate": "换手率 %", "turnover": "成交额（亿元）",
    "dividend_yield": "股息率 %", "price": "股价（元）",
}


@tool(
    "screen_stocks",
    "全市场条件选股（最新快照）。可按行业与数值区间过滤，按任一字段排序。字段："
    + "；".join(f"{k}={v}" for k, v in _SCREEN_FIELDS.items()),
    {
        "industry": {"type": "string", "description": "行业名（完全匹配），可省略"},
        "filters": {
            "type": "object",
            "description": '数值区间，如 {"pe": {"min": 0, "max": 30}, "market_cap": {"min": 100}}',
        },
        "sort_by": {"type": "string", "description": "排序字段，默认 change_pct"},
        "ascending": {"type": "boolean", "description": "是否升序，默认降序"},
        "limit": {"type": "integer", "description": "返回条数，默认 20，最多 50"},
        "exclude_st": {"type": "boolean", "description": "排除 ST，默认 true"},
    },
)
def _screen_stocks(ctx: Ctx, args: dict[str, Any]) -> Any:
    db = ctx.db
    sort_by = args.get("sort_by") or "change_pct"
    if sort_by not in _SCREEN_FIELDS:
        raise ValueError(f"sort_by 只能是 {list(_SCREEN_FIELDS)}")
    filters = args.get("filters") or {}
    if not isinstance(filters, dict) or any(k not in _SCREEN_FIELDS for k in filters):
        raise ValueError(f"filters 字段只能是 {list(_SCREEN_FIELDS)}")
    info = _names(db)
    divs = {f.code: f.dividend_yield for f in db.execute(select(Fundamental)).scalars()}
    rows = []
    for q in db.execute(select(Quote)).scalars():
        meta = info.get(q.code)
        if meta is None or meta.delist_date is not None or not q.price:
            continue
        if args.get("exclude_st", True) and "ST" in meta.name:
            continue
        if args.get("industry") and meta.industry != args["industry"]:
            continue
        row = {
            "code": q.code, "name": meta.name, "industry": meta.industry, "price": q.price,
            "change_pct": change_pct(q.price, q.prev_close), "pe": _r(q.pe), "pb": _r(q.pb), "roe": _r(q.roe),
            "market_cap": _r(q.market_cap, 1), "turnover_rate": _r(q.turnover_rate), "turnover": _r(q.turnover),
            "dividend_yield": _r(divs.get(q.code)),
        }
        ok = True
        for k, rng in filters.items():
            v = row.get(k)
            rng = rng if isinstance(rng, dict) else {}
            if v is None or ("min" in rng and v < rng["min"]) or ("max" in rng and v > rng["max"]):
                ok = False
                break
        if ok:
            rows.append(row)
    sign = 1 if args.get("ascending") else -1
    rows.sort(key=lambda r: (r[sort_by] is None, sign * (r[sort_by] or 0)))
    return {"matched": len(rows), "items": rows[: _limit(args, 20, 50)]}


@tool(
    "get_stock",
    "个股详情：最新行情、技术面（均线 / RSI / 区间涨幅 / 波动率 / 一年高低位）、估值、近四期财报要点、"
    "两融余额、未来 60 日解禁、Kairos 研报评级。",
    {"code": {"type": "string"}},
    ["code"],
)
def _get_stock(ctx: Ctx, args: dict[str, Any]) -> Any:
    db = ctx.db
    code = _code(args)
    meta = db.get(StockInfo, code)
    if meta is None:
        raise ValueError(f"找不到 {code}")
    q = db.get(Quote, code)
    bars = db.execute(
        select(Kline.ts, Kline.close, Kline.volume).where(Kline.code == code, Kline.period == "1d")
        .order_by(Kline.ts.desc()).limit(250)
    ).all()[::-1]
    closes = [b[1] for b in bars]
    rets = [b / a - 1 for a, b in zip(closes[-21:-1], closes[-20:])] if len(closes) > 21 else []
    vol20 = (sum(r * r for r in rets) / len(rets)) ** 0.5 * (252 ** 0.5) * 100 if rets else None
    basic = db.execute(
        select(DailyBasic).where(DailyBasic.code == code).order_by(DailyBasic.trade_date.desc()).limit(1)
    ).scalar()
    fins = db.execute(
        select(FinReport).where(FinReport.code == code).order_by(FinReport.report_date.desc()).limit(4)
    ).scalars().all()
    margin = db.execute(
        select(MarginTrading).where(MarginTrading.code == code).order_by(MarginTrading.trade_date.desc()).limit(6)
    ).scalars().all()
    today = broker.cst_today().date()
    lockups = db.execute(
        select(Lockup).where(Lockup.code == code, Lockup.free_date >= today,
                             Lockup.free_date <= today + timedelta(days=60))
    ).scalars().all()
    report = db.execute(
        select(ResearchReport).where(ResearchReport.code == code, ResearchReport.status == "done")
        .order_by(ResearchReport.id.desc()).limit(1)
    ).scalar()
    return {
        "code": code, "name": meta.name, "industry": meta.industry,
        "quote": {
            "price": q.price, "changePct": change_pct(q.price, q.prev_close), "open": q.open, "high": q.high,
            "low": q.low, "turnoverYi": _r(q.turnover), "turnoverRate": _r(q.turnover_rate),
            "pe": _r(q.pe), "pb": _r(q.pb), "marketCapYi": _r(q.market_cap, 1), "asOf": str(q.ts)[:16],
        } if q else None,
        "technical": {
            "lastDate": bars[-1][0].strftime("%Y-%m-%d") if bars else None,
            "ma5": _ma(closes, 5), "ma10": _ma(closes, 10), "ma20": _ma(closes, 20), "ma60": _ma(closes, 60),
            "rsi14": _rsi(closes),
            "ret5d": _pct(closes[-1], closes[-6]) if len(closes) > 5 else None,
            "ret20d": _pct(closes[-1], closes[-21]) if len(closes) > 20 else None,
            "ret60d": _pct(closes[-1], closes[-61]) if len(closes) > 60 else None,
            "vol20dAnnualized": _r(vol20),
            "high250": _r(max(closes)) if closes else None, "low250": _r(min(closes)) if closes else None,
            "volRatio5to20": _r(
                (sum(b[2] for b in bars[-5:]) / 5) / (sum(b[2] for b in bars[-20:]) / 20)
            ) if len(bars) >= 20 and sum(b[2] for b in bars[-20:]) else None,
        },
        "valuation": {
            "date": str(basic.trade_date), "peTtm": _r(basic.pe_ttm), "pbMrq": _r(basic.pb_mrq),
            "psTtm": _r(basic.ps_ttm), "isST": basic.is_st, "suspended": basic.suspended,
        } if basic else None,
        "financials": [
            {"period": str(f.report_date), "notice": str(f.notice_date), "revenueYoy": _r(f.revenue_yoy),
             "profitYoy": _r(f.profit_yoy), "roe": _r(f.roe), "grossMargin": _r(f.gross_margin), "eps": _r(f.eps, 3)}
            for f in fins
        ],
        "margin": [{"date": str(m.trade_date), "rzyeYi": _r((m.rzye or 0) / 1e8)} for m in margin],
        "lockupsNext60d": [
            {"date": str(lk.free_date), "type": lk.shares_type, "floatRatioPct": _r(lk.float_ratio)} for lk in lockups
        ],
        "kairosResearch": {
            "grade": report.grade, "score": report.total_score, "asOf": report.data_as_of,
            "redFlags": [f.get("label") for f in (report.red_flags or [])][:5],
        } if report else None,
    }


@tool(
    "get_kline",
    "个股日 K（前复权，成交量单位：股），按时间升序。",
    {"code": {"type": "string"}, "days": {"type": "integer", "description": "根数，默认 30，最多 120"}},
    ["code"],
)
def _get_kline(ctx: Ctx, args: dict[str, Any]) -> Any:
    code = _code(args)
    try:
        n = max(1, min(int(args.get("days") or 30), 120))
    except (TypeError, ValueError):
        n = 30
    rows = ctx.db.execute(
        select(Kline.ts, Kline.open, Kline.high, Kline.low, Kline.close, Kline.volume)
        .where(Kline.code == code, Kline.period == "1d").order_by(Kline.ts.desc()).limit(n)
    ).all()[::-1]
    return [
        {"d": r[0].strftime("%Y-%m-%d"), "o": _r(r[1]), "h": _r(r[2]), "l": _r(r[3]), "c": _r(r[4]), "v": r[5]}
        for r in rows
    ]


@tool(
    "get_announcements",
    "个股近期公告标题（可带摘要）。",
    {"code": {"type": "string"}, "days": {"type": "integer", "description": "回看天数，默认 30"}},
    ["code"],
)
def _get_announcements(ctx: Ctx, args: dict[str, Any]) -> Any:
    code = _code(args)
    days = max(1, min(int(args.get("days") or 30), 365))
    since = broker.cst_today().date() - timedelta(days=days)
    rows = ctx.db.execute(
        select(Announcement).where(Announcement.code == code, Announcement.ann_date >= since)
        .order_by(Announcement.ann_date.desc()).limit(30)
    ).scalars().all()
    return [
        {"date": str(a.ann_date), "title": a.title, "category": a.category, "summary": (a.summary or "")[:300]}
        for a in rows
    ]


@tool(
    "sector_overview",
    "行业板块全景：近 1 / 5 / 20 日涨幅、拥挤度（0~1，越高越拥挤）、最新 AI 叙事打分、月度配置建议。",
)
def _sector_overview(ctx: Ctx, args: dict[str, Any]) -> Any:
    db = ctx.db
    sectors = {s.code: s.name for s in db.execute(select(SectorInfo)).scalars()}
    if not sectors:
        return {"note": "暂无行业板块数据"}
    last = db.execute(select(func.max(SectorBar.ts))).scalar()
    days = [r[0] for r in db.execute(
        select(SectorBar.ts).distinct().where(SectorBar.ts <= last).order_by(SectorBar.ts.desc()).limit(21)
    )]
    closes: dict[str, dict[datetime, float]] = defaultdict(dict)
    for code, ts, close in db.execute(
        select(SectorBar.code, SectorBar.ts, SectorBar.close).where(SectorBar.ts.in_(days))
    ):
        closes[code][ts] = close
    metric_day = db.execute(select(func.max(SectorMetric.ts))).scalar()
    crowd = {m.code: m.crowd for m in db.execute(select(SectorMetric).where(SectorMetric.ts == metric_day)).scalars()}
    nar_day = db.execute(select(func.max(SectorNarrative.date)).where(SectorNarrative.code != "_market")).scalar()
    nar = {n.code: n for n in db.execute(select(SectorNarrative).where(SectorNarrative.date == nar_day)).scalars()}
    tilt_month = db.execute(select(func.max(SectorTilt.month))).scalar()
    tilt = {t.code: t.suggestion for t in db.execute(select(SectorTilt).where(SectorTilt.month == tilt_month)).scalars()}

    def ret(c: dict[datetime, float], k: int) -> float | None:
        return _pct(c.get(days[0]), c.get(days[k])) if len(days) > k else None

    rows = []
    for code, name in sectors.items():
        c = closes.get(code, {})
        n = nar.get(code)
        rows.append({
            "name": name, "ret1d": ret(c, 1), "ret5d": ret(c, 5), "ret20d": ret(c, 20),
            "crowd": _r(crowd.get(code)), "tilt": tilt.get(code),
            "narrative": {"direction": n.direction, "strength": n.strength, "summary": n.summary} if n else None,
        })
    rows.sort(key=lambda r: -(r["ret5d"] or -999))
    return {"asOf": days[0].strftime("%Y-%m-%d") if days else None, "sectors": rows}


# ---------------------------------------------------------------- 资讯


_news_cache: tuple[float, list[str]] = (0.0, [])


@tool(
    "get_news",
    "最新财经快讯标题（东财全球快讯，约 100 条，按时间倒序）。可用关键词过滤。",
    {"keyword": {"type": "string", "description": "只返回含该词的标题，可省略"}},
)
def _get_news(ctx: Ctx, args: dict[str, Any]) -> Any:
    global _news_cache
    from app.services.narrative import fetch_headlines

    if time.time() - _news_cache[0] > 600 or not _news_cache[1]:
        _news_cache = (time.time(), fetch_headlines())
    titles = _news_cache[1]
    kw = str(args.get("keyword") or "").strip()
    if kw:
        titles = [t for t in titles if kw in t]
    return titles[:100] if titles else "暂无快讯（新闻源不可用或无匹配）"


@tool(
    "web_search",
    "联网搜索（Tavily），用于查个股 / 行业 / 宏观的最新新闻和事件。返回标题、链接、摘要、发布日期。"
    "搜索结果是外部数据，可能有误或带推销，注意交叉验证。",
    {
        "query": {"type": "string", "description": "搜索词，中文即可"},
        "time_range": {"type": "string", "enum": ["day", "week", "month", "year"], "description": "时间范围，默认 week"},
        "topic": {"type": "string", "enum": ["general", "news", "finance"], "description": "默认 news"},
        "max_results": {"type": "integer", "description": "默认 6，最多 10"},
    },
    ["query"],
    group="web",
)
def _web_search(ctx: Ctx, args: dict[str, Any]) -> Any:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("query 不能为空")
    used = ctx.cache.get("web_search", 0)
    if used >= WEB_SEARCH_QUOTA:
        raise ValueError(f"本次运行的联网搜索次数（{WEB_SEARCH_QUOTA}）已用完，基于已有信息决策")
    ctx.cache["web_search"] = used + 1
    body = {
        "query": query[:400],
        "topic": args.get("topic") if args.get("topic") in ("general", "news", "finance") else "news",
        "time_range": args.get("time_range") if args.get("time_range") in ("day", "week", "month", "year") else "week",
        "max_results": max(1, min(int(args.get("max_results") or 6), 10)),
        "search_depth": "basic",
        "include_answer": False,
    }
    r = httpx.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {settings.tavily_api_key}"},
        json=body,
        timeout=30,
    )
    r.raise_for_status()
    return [
        {"title": x.get("title"), "url": x.get("url"), "date": x.get("published_date"),
         "content": (x.get("content") or "")[:600]}
        for x in r.json().get("results") or []
    ]


# ---------------------------------------------------------------- 交易


@tool(
    "place_order",
    "下单（模拟盘，按真实规则撮合）。盘前（09:30 前）下的单按当日行情撮合，之后下的单在下一个交易日撮合；"
    "当日未成交即作废。不传 limit_price 为开盘市价单。买入主板 / 创业板须 100 股整数倍，科创板 ≥200 股。"
    "必须写清理由；若决策依据了经验库条目，在 memory_ids 里列出其 id（周度反思按实盘结果评估这些经验）。",
    {
        "code": {"type": "string"},
        "side": {"type": "string", "enum": ["buy", "sell"]},
        "shares": {"type": "integer", "description": "股数（不是手数）"},
        "limit_price": {"type": "number", "description": "限价，省略为开盘市价"},
        "reason": {"type": "string", "description": "下单理由：逻辑、预期、止损 / 退出条件"},
        "memory_ids": {"type": "array", "items": {"type": "integer"}, "description": "引用的经验条目 id"},
    },
    ["code", "side", "shares", "reason"],
    group="trade",
)
def _place_order(ctx: Ctx, args: dict[str, Any]) -> Any:
    limit = args.get("limit_price")
    o = broker.place_order(
        ctx.db, ctx.account, code=str(args.get("code") or ""), side=str(args.get("side") or ""),
        shares=args.get("shares"), limit_price=float(limit) if limit not in (None, "", 0) else None,
        reason=str(args.get("reason") or ""), memory_ids=args.get("memory_ids") or [],
        session_id=ctx.session.id,
    )
    return {"ok": True, "orderId": o.id, "name": o.name, "status": o.status}


@tool(
    "cancel_order",
    "撤销一笔尚未撮合的挂单。",
    {"order_id": {"type": "integer"}},
    ["order_id"],
    group="trade",
)
def _cancel_order(ctx: Ctx, args: dict[str, Any]) -> Any:
    o = ctx.db.get(AgentOrder, int(args.get("order_id") or 0))
    if o is None or o.account_id != ctx.account.id:
        raise ValueError("订单不存在")
    if o.status != "pending":
        raise ValueError(f"订单状态为 {o.status}，不能撤销")
    o.status, o.note = "cancelled", "agent 撤单"
    ctx.db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- 经验库


def memory_row(m: AgentMemory) -> dict[str, Any]:
    return {"id": m.id, "kind": m.kind, "content": m.content, "tags": m.tags or [],
            "created": (m.created_at + timedelta(hours=8)).strftime("%Y-%m-%d") if m.created_at else None}


@tool(
    "recall_memories",
    "检索经验库（自己过去沉淀的教训 / 规则 / 观察）。系统提示里只放了最近的一部分，需要更多时用它按关键词或标签找。",
    {"keyword": {"type": "string"}, "tag": {"type": "string"},
     "include_retired": {"type": "boolean", "description": "是否包含已淘汰的条目，默认否"},
     "limit": {"type": "integer", "description": "默认 20，最多 60"}},
)
def _recall_memories(ctx: Ctx, args: dict[str, Any]) -> Any:
    stmt = select(AgentMemory).where(AgentMemory.account_id == ctx.account.id)
    if not args.get("include_retired"):
        stmt = stmt.where(AgentMemory.active.is_(True))
    if args.get("keyword"):
        stmt = stmt.where(AgentMemory.content.contains(str(args["keyword"])))
    rows = ctx.db.execute(stmt.order_by(AgentMemory.id.desc()).limit(200)).scalars().all()
    if args.get("tag"):
        rows = [m for m in rows if str(args["tag"]) in (m.tags or [])]
    out = []
    for m in rows[: _limit(args, 20, 60)]:
        row = memory_row(m)
        if not m.active:
            row["retired"] = m.retire_reason
        out.append(row)
    return out


@tool(
    "save_memory",
    "把一条可复用的经验写进经验库，供以后的自己参考。要具体、可执行、可证伪"
    "（如“缩量阴跌中的低估值股，跌破 60 日线后平均还要再跌 8%，不要左侧抄底”），不要写空话或当天流水账。",
    {
        "content": {"type": "string"},
        "kind": {"type": "string", "enum": ["lesson", "rule", "watch"],
                 "description": "lesson=从结果得出的教训；rule=打算遵守的规则；watch=需要持续跟踪的观察"},
        "tags": {"type": "array", "items": {"type": "string"}, "description": "如 止损 / 追高 / 半导体 / 情绪"},
    },
    ["content", "kind"],
    group="memory",
)
def _save_memory(ctx: Ctx, args: dict[str, Any]) -> Any:
    content = str(args.get("content") or "").strip()
    if len(content) < 8:
        raise ValueError("内容太短，写具体一点")
    kind = args.get("kind") if args.get("kind") in ("lesson", "rule", "watch") else "lesson"
    tags = [str(t)[:20] for t in (args.get("tags") or [])][:6]
    m = AgentMemory(account_id=ctx.account.id, kind=kind, content=content[:1000], tags=tags,
                    session_id=ctx.session.id)
    ctx.db.add(m)
    ctx.db.commit()
    return {"ok": True, "id": m.id}


@tool(
    "retire_memory",
    "淘汰一条经验（被实盘证伪、已过时、或已合并进新条目 / 交易手册）。写明原因。",
    {"memory_id": {"type": "integer"}, "reason": {"type": "string"}},
    ["memory_id", "reason"],
    group="memory",
)
def _retire_memory(ctx: Ctx, args: dict[str, Any]) -> Any:
    m = ctx.db.get(AgentMemory, int(args.get("memory_id") or 0))
    if m is None or m.account_id != ctx.account.id:
        raise ValueError("经验条目不存在")
    m.active = False
    m.retire_reason = str(args.get("reason") or "")[:255]
    m.retired_at = datetime.now(timezone.utc)
    ctx.db.commit()
    return {"ok": True}


@tool(
    "update_playbook",
    "改写交易手册（整篇替换）。手册是你每次运行都会读到的核心投资框架：选股逻辑、仓位与风控纪律、"
    "买卖条件、要回避的错误。只保留被实盘验证有效的内容，控制在 1500 字内。",
    {"content": {"type": "string", "description": "新版手册全文（Markdown）"},
     "rationale": {"type": "string", "description": "相对上一版改了什么、依据哪些实盘结果"}},
    ["content", "rationale"],
    group="playbook",
)
def _update_playbook(ctx: Ctx, args: dict[str, Any]) -> Any:
    content = str(args.get("content") or "").strip()
    if len(content) < 50:
        raise ValueError("手册内容过短")
    acct = ctx.account
    acct.playbook = content[:6000]
    acct.playbook_version += 1
    ctx.db.add(AgentPlaybook(account_id=acct.id, version=acct.playbook_version, content=acct.playbook,
                             rationale=str(args.get("rationale") or "")[:2000], session_id=ctx.session.id))
    ctx.db.commit()
    return {"ok": True, "version": acct.playbook_version}
