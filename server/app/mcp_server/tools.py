"""MCP 数据工具：全部只读，数据来自本地库（行情、日 K、每日指标、财务、股东、交易、公告、快讯、宏观）。

每个工具是普通同步函数，参数即工具入参（类型注解 + 文档字符串生成给 agent 看的 schema）。
鉴权、计量、放到线程池执行由 server.py 统一包一层。时间序列返回 {"columns": [...], "rows": [[...]]}
压缩体积；其余返回字典列表。
"""


import re
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.market import Kline, Quote, StockInfo
from app.services.market import change_pct
from kairos_data import schema as S
from kairos_data.sync import INDEXES, MACRO


class UserError(Exception):
    """入参或数据问题，原样返回给 agent（不是服务故障）。"""


MAX_ROWS = 1000
INDEX_NAMES = {code: name for code, (name, *_rest) in INDEXES.items()}

Code = Annotated[str, Field(description="6 位证券代码，如 600519；也接受 sh600519 / 600519.SH")]
DateStr = Annotated[str | None, Field(description="日期 YYYY-MM-DD，可省略")]
Limit = Annotated[int, Field(ge=1, le=MAX_ROWS, description=f"最多返回行数（≤{MAX_ROWS}）")]


def _code(raw: str) -> str:
    m = re.search(r"\d{6}", raw or "")
    if not m:
        raise UserError(f"无法识别的证券代码：{raw!r}，应为 6 位数字")
    return m.group(0)


def _date(raw: str | None, name: str) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError as exc:
        raise UserError(f"{name} 应为 YYYY-MM-DD，收到 {raw!r}") from exc


def _v(x: Any) -> Any:
    if isinstance(x, datetime):
        return x.date().isoformat() if x.hour == x.minute == x.second == 0 and x.tzinfo is None else x.isoformat()
    if isinstance(x, date):
        return x.isoformat()
    return x


def _dicts(rows) -> list[dict]:  # noqa: ANN001
    return [{k: _v(v) for k, v in r._mapping.items()} for r in rows]


def _series(rows, columns: list[str]) -> dict:  # noqa: ANN001
    """按日期倒序查出的最近 N 行 → 正序的列式表。"""
    data = [[_v(v) for v in r] for r in rows]
    data.reverse()
    return {"columns": columns, "rows": data, "count": len(data)}


def _range(stmt, col, start: str | None, end: str | None):  # noqa: ANN001, ANN202
    s, e = _date(start, "start_date"), _date(end, "end_date")
    if s:
        stmt = stmt.where(col >= s)
    if e:
        stmt = stmt.where(col <= e)
    return stmt


def _stock(db: Session, code: str) -> StockInfo:
    info = db.get(StockInfo, code)
    if info is None:
        raise UserError(f"没有这只股票：{code}（可先用 search_securities 查代码）")
    return info


# ---------------------------------------------------------------- 证券与行情


def search_securities(db: Session, query: Annotated[str, Field(min_length=1, max_length=32,
                      description="代码前缀或名称片段，如 600519 / 茅台 / 300 ETF")],
                      limit: Annotated[int, Field(ge=1, le=50)] = 20) -> list[dict]:
    """按代码前缀或名称搜索 A 股（含退市）、ETF、可转债；在市的排前面。"""
    q = query.strip()
    stocks = db.execute(
        select(StockInfo).where(or_(StockInfo.code.startswith(q), StockInfo.name.contains(q)))
        .order_by(StockInfo.delist_date.is_not(None), StockInfo.code).limit(limit)
    ).scalars().all()
    others = db.execute(
        select(S.security_info).where(or_(S.security_info.c.code.startswith(q), S.security_info.c.name.contains(q)))
        .order_by(S.security_info.c.delist_date.is_not(None), S.security_info.c.code).limit(limit)
    ).all()
    hits = [{"code": s.code, "name": s.name, "type": "stock", "market": s.market, "industry": s.industry,
             "list_date": _v(s.list_date), "delist_date": _v(s.delist_date)} for s in stocks]
    hits += [{"code": s.code, "name": s.name, "type": s.sec_type, "market": s.exchange,
              "list_date": _v(s.list_date), "delist_date": _v(s.delist_date)} for s in others]
    hits.sort(key=lambda h: (h["delist_date"] is not None, h["type"] != "stock", h["code"]))
    return hits[:limit]


def get_stock_profile(db: Session, code: Code) -> dict:
    """个股概况：名称、市场、申万行业、上市 / 退市日、最新行情快照与估值、日 K 覆盖范围、所属主要指数。"""
    c = _code(code)
    info = _stock(db, c)
    q = db.get(Quote, c)
    first, last, n = db.execute(
        select(func.min(Kline.ts), func.max(Kline.ts), func.count()).where(Kline.code == c, Kline.period == "1d")
    ).one()
    ic = S.index_constituent
    latest = select(ic.c.index_code, func.max(ic.c.snapshot_date).label("snap")).group_by(ic.c.index_code).subquery()
    member_of = db.execute(
        select(ic.c.index_code, ic.c.weight).join(
            latest, (latest.c.index_code == ic.c.index_code) & (latest.c.snap == ic.c.snapshot_date))
        .where(ic.c.code == c)
    ).all()
    return {
        "code": c, "name": info.name, "market": info.market, "industry": info.industry,
        "list_date": _v(info.list_date), "delist_date": _v(info.delist_date),
        "quote": None if q is None or info.delist_date else {
            "price": q.price, "prev_close": q.prev_close, "change_pct": change_pct(q.price, q.prev_close),
            "pe": q.pe, "pb": q.pb, "market_cap_yi": q.market_cap, "turnover_rate_pct": q.turnover_rate,
            "turnover_yi": q.turnover, "ts": _v(q.ts),
        },
        "kline_range": {"first": _v(first), "last": _v(last), "bars": n},
        "indexes": [{"index_code": i, "name": INDEX_NAMES.get(i, i), "weight_pct": w} for i, w in member_of],
    }


def get_realtime_quotes(db: Session, codes: Annotated[list[str], Field(min_length=1, max_length=50,
                        description="证券代码列表，最多 50 个")]) -> list[dict]:
    """最新行情快照（交易时段约每分钟更新，收盘后为收盘价）。成交额、市值单位亿元，换手率 %。"""
    cs = list(dict.fromkeys(_code(c) for c in codes))
    names = dict(db.execute(select(StockInfo.code, StockInfo.name).where(StockInfo.code.in_(cs))).tuples().all())
    out = []
    for q in db.execute(select(Quote).where(Quote.code.in_(cs))).scalars():
        out.append({"code": q.code, "name": names.get(q.code, q.code), "price": q.price,
                    "change_pct": change_pct(q.price, q.prev_close), "open": q.open, "high": q.high, "low": q.low,
                    "prev_close": q.prev_close, "volume_shares": q.volume * 100, "turnover_yi": q.turnover,
                    "turnover_rate_pct": q.turnover_rate, "pe": q.pe, "pb": q.pb, "market_cap_yi": q.market_cap,
                    "ts": _v(q.ts)})
    return out


def get_kline(db: Session, code: Code, start_date: DateStr = None, end_date: DateStr = None,
              limit: Limit = 250) -> dict:
    """日 K 线（按日期正序，取区间内最近 limit 根）。A 股为前复权、成交量单位股；
    ETF / 可转债为不复权，成交量单位手（ETF）/ 张（转债），另有成交额（元）。"""
    c = _code(code)
    if db.get(StockInfo, c) is not None:
        k = Kline
        stmt = _range(select(k.ts, k.open, k.high, k.low, k.close, k.volume)
                      .where(k.code == c, k.period == "1d"), k.ts, start_date, end_date)
        rows = db.execute(stmt.order_by(k.ts.desc()).limit(limit)).all()
        return {"code": c, "adjust": "qfq", **_series(rows, ["date", "open", "high", "low", "close", "volume"])}
    sb = S.security_bar
    if db.execute(select(S.security_info.c.code).where(S.security_info.c.code == c)).first() is None:
        raise UserError(f"没有这只证券：{c}（可先用 search_securities 查代码）")
    stmt = _range(select(sb.c.trade_date, sb.c.open, sb.c.high, sb.c.low, sb.c.close, sb.c.volume, sb.c.amount)
                  .where(sb.c.code == c), sb.c.trade_date, start_date, end_date)
    rows = db.execute(stmt.order_by(sb.c.trade_date.desc()).limit(limit)).all()
    return {"code": c, "adjust": "none",
            **_series(rows, ["date", "open", "high", "low", "close", "volume", "amount"])}


def list_indices(db: Session) -> list[dict]:
    """收录的主要指数及最新收盘、涨跌幅。"""
    ib = S.index_bar
    out = []
    for code, name in INDEX_NAMES.items():
        last2 = db.execute(select(ib.c.trade_date, ib.c.close).where(ib.c.index_code == code)
                           .order_by(ib.c.trade_date.desc()).limit(2)).all()
        out.append({"index_code": code, "name": name, "date": _v(last2[0][0]) if last2 else None,
                    "close": last2[0][1] if last2 else None,
                    "change_pct": change_pct(last2[0][1], last2[1][1]) if len(last2) == 2 else None})
    return out


def get_index_bars(db: Session, index_code: Annotated[str, Field(description="指数代码，见 list_indices，如 000300")],
                   start_date: DateStr = None, end_date: DateStr = None, limit: Limit = 250) -> dict:
    """指数日线（按日期正序）。成交量单位手，成交额元。"""
    c = _code(index_code)
    if c not in INDEX_NAMES:
        raise UserError(f"未收录的指数 {c}，可用：{', '.join(f'{k} {v}' for k, v in INDEX_NAMES.items())}")
    ib = S.index_bar
    stmt = _range(select(ib.c.trade_date, ib.c.open, ib.c.high, ib.c.low, ib.c.close, ib.c.volume, ib.c.amount)
                  .where(ib.c.index_code == c), ib.c.trade_date, start_date, end_date)
    rows = db.execute(stmt.order_by(ib.c.trade_date.desc()).limit(limit)).all()
    return {"index_code": c, "name": INDEX_NAMES[c],
            **_series(rows, ["date", "open", "high", "low", "close", "volume", "amount"])}


def get_index_constituents(db: Session, index_code: Annotated[str, Field(description="指数代码，如 000300")],
                           as_of: DateStr = None) -> dict:
    """指数成分：取不晚于 as_of（默认今天）的最近一份名单；有权重时按权重降序（权重 %）。"""
    c = _code(index_code)
    ic = S.index_constituent
    day = _date(as_of, "as_of") or date.today()
    snap = db.execute(select(func.max(ic.c.snapshot_date)).where(ic.c.index_code == c, ic.c.snapshot_date <= day)
                      ).scalar()
    if snap is None:
        raise UserError(f"指数 {c} 在 {day} 之前没有成分数据（收录的是沪深 300 / 中证 500 等宽基指数）")
    rows = db.execute(
        select(ic.c.code, StockInfo.name, StockInfo.industry, ic.c.weight)
        .outerjoin(StockInfo, StockInfo.code == ic.c.code)
        .where(ic.c.index_code == c, ic.c.snapshot_date == snap)
    ).all()
    members = sorted(({"code": r[0], "name": r[1], "industry": r[2], "weight_pct": r[3]} for r in rows),
                     key=lambda m: (-(m["weight_pct"] or 0), m["code"]))
    return {"index_code": c, "name": INDEX_NAMES.get(c, c), "snapshot_date": snap.isoformat(),
            "count": len(members), "members": members}


def list_industries(db: Session) -> list[dict]:
    """申万行业列表（股票池当前分类）及在市股票数。"""
    rows = db.execute(
        select(StockInfo.industry, func.count()).where(StockInfo.delist_date.is_(None), StockInfo.industry != "—")
        .group_by(StockInfo.industry).order_by(func.count().desc())
    ).all()
    return [{"industry": i, "stocks": n} for i, n in rows]


# ---------------------------------------------------------------- 每日指标与估值

_DB_COLS = ["close", "preclose", "volume", "amount", "turn", "suspended", "is_st",
            "pe_ttm", "pb_mrq", "ps_ttm", "pcf_ttm", "total_mv"]
_DB_NOTE = ("close / preclose 不复权（元）；volume 股；amount 元；turn 换手率 %；total_mv 总市值（元）；"
            "pe_ttm / pb_mrq / ps_ttm / pcf_ttm 按当日收盘价计")


def get_daily_basic(db: Session, code: Code, start_date: DateStr = None, end_date: DateStr = None,
                    limit: Limit = 250) -> dict:
    """个股每日指标历史（按日期正序，取区间内最近 limit 天）：不复权价、成交、换手、ST / 停牌、
    PE(TTM) / PB(MRQ) / PS(TTM) / PCF(TTM)、总市值。沪深 1990 年起，北交所 2026-07 起。"""
    c = _code(code)
    t = S.daily_basic
    stmt = _range(select(t.c.trade_date, *(t.c[x] for x in _DB_COLS)).where(t.c.code == c),
                  t.c.trade_date, start_date, end_date)
    rows = db.execute(stmt.order_by(t.c.trade_date.desc()).limit(limit)).all()
    return {"code": c, "note": _DB_NOTE, **_series(rows, ["date", *_DB_COLS])}


_SORTABLE = ("total_mv", "pe_ttm", "pb_mrq", "ps_ttm", "turn", "amount")


def get_market_daily_basic(
    db: Session,
    trade_date: DateStr = None,
    industry: Annotated[str | None, Field(description="只看某个申万行业（list_industries 里的名称）")] = None,
    codes: Annotated[list[str] | None, Field(max_length=200, description="只看这些代码（≤200）")] = None,
    sort_by: Literal["total_mv", "pe_ttm", "pb_mrq", "ps_ttm", "turn", "amount"] = "total_mv",
    ascending: bool = False,
    limit: Annotated[int, Field(ge=1, le=500)] = 100,
) -> dict:
    """某个交易日的全市场每日指标截面（默认最近交易日），可按行业 / 代码筛选、按指标排序，跳过停牌股。
    排序字段为空值的股票排在最后。"""
    t = S.daily_basic
    day = _date(trade_date, "trade_date") or db.execute(select(func.max(t.c.trade_date))).scalar()
    if day is None:
        raise UserError("没有每日指标数据")
    stmt = (select(t.c.code, StockInfo.name, StockInfo.industry, *(t.c[x] for x in _DB_COLS if x != "suspended"))
            .join(StockInfo, StockInfo.code == t.c.code)
            .where(t.c.trade_date == day, t.c.suspended.is_(False)))
    if industry:
        stmt = stmt.where(StockInfo.industry == industry)
    if codes:
        stmt = stmt.where(t.c.code.in_([_code(c) for c in codes]))
    col = t.c[sort_by]
    stmt = stmt.order_by(col.is_(None), col.asc() if ascending else col.desc()).limit(limit)
    rows = _dicts(db.execute(stmt).all())
    return {"trade_date": day.isoformat(), "note": _DB_NOTE, "count": len(rows), "rows": rows}


# ---------------------------------------------------------------- 财务与股东


def get_financial_indicators(db: Session, code: Code,
                             limit: Annotated[int, Field(ge=1, le=80)] = 12) -> list[dict]:
    """定期报告主要指标（按报告期倒序）：EPS、扣非 EPS、营收与归母净利（元）及同比 %、加权 ROE %、
    每股净资产、每股经营现金流、毛利率 %、分红预案、公告日。"""
    c = _code(code)
    t = S.fin_report
    rows = db.execute(select(t).where(t.c.code == c).order_by(t.c.report_date.desc()).limit(limit)).all()
    return [{k: v for k, v in r.items() if k not in ("code", "updated_at")} for r in _dicts(rows)]


_STMT_GROUPS = {"balance": "bs", "income": "is", "cashflow": "cf"}


def get_financial_statements(db: Session, code: Code,
                             statement: Literal["balance", "income", "cashflow"] = "income",
                             limit: Annotated[int, Field(ge=1, le=80)] = 8) -> dict:
    """三大报表关键科目（按报告期倒序，金额单位元；利润表、现金流量表为年初至报告期末累计值）。
    statement：balance 资产负债表 / income 利润表 / cashflow 现金流量表。"""
    c = _code(code)
    t = S.fin_statement
    items = {k: label for k, (grp, _src, label) in S.STATEMENT_ITEMS.items() if grp == _STMT_GROUPS[statement]}
    rows = db.execute(select(t.c.report_date, t.c.notice_date, *(t.c[k] for k in items))
                      .where(t.c.code == c).order_by(t.c.report_date.desc()).limit(limit)).all()
    return {"code": c, "statement": statement, "labels": items, "rows": _dicts(rows)}


def get_dividends(db: Session, code: Code) -> list[dict]:
    """历年分红送转（按报告期倒序）：每 10 股派现（税前，元）/ 送股 / 转增，预案公告日、股权登记日、除权除息日、进度。"""
    c = _code(code)
    t = S.dividend
    rows = db.execute(select(t).where(t.c.code == c).order_by(t.c.report_date.desc())).all()
    return [{k: v for k, v in r.items() if k != "code"} for r in _dicts(rows)]


def get_share_capital(db: Session, code: Code, limit: Annotated[int, Field(ge=1, le=200)] = 20) -> list[dict]:
    """股本变动（按变动日倒序）：总股本、已流通 A 股、限售股（股）及变动原因。"""
    c = _code(code)
    t = S.share_capital
    rows = db.execute(select(t).where(t.c.code == c).order_by(t.c.change_date.desc()).limit(limit)).all()
    return [{k: v for k, v in r.items() if k != "code"} for r in _dicts(rows)]


def get_top_holders(db: Session, code: Code, kind: Literal["all", "float"] = "all",
                    periods: Annotated[int, Field(ge=1, le=8)] = 1) -> list[dict]:
    """十大股东（kind=all，占总股本 %）或十大流通股东（kind=float，占流通股 %），最近 periods 个报告期。
    持股数单位股，change 为相对上期的新进 / 增加 / 减少 / 不变。"""
    c = _code(code)
    t = S.top_holder
    ends = list(db.execute(select(t.c.end_date).where(t.c.code == c, t.c.kind == kind).distinct()
                           .order_by(t.c.end_date.desc()).limit(periods)).scalars())
    if not ends:
        return []
    rows = db.execute(select(t.c.end_date, t.c.rank, t.c.holder_name, t.c.holder_type, t.c.shares_type,
                             t.c.hold_num, t.c.hold_ratio, t.c.change, t.c.notice_date)
                      .where(t.c.code == c, t.c.kind == kind, t.c.end_date.in_(ends))
                      .order_by(t.c.end_date.desc(), t.c.rank)).all()
    return _dicts(rows)


def get_holder_count(db: Session, code: Code, limit: Annotated[int, Field(ge=1, le=200)] = 20) -> list[dict]:
    """股东户数（按统计截止日倒序）：户数、户均持股（股）、A 股总股本。"""
    c = _code(code)
    t = S.holder_count
    rows = db.execute(select(t).where(t.c.code == c).order_by(t.c.end_date.desc()).limit(limit)).all()
    return [{k: v for k, v in r.items() if k != "code"} for r in _dicts(rows)]


def get_lockups(db: Session, code: Annotated[str | None, Field(description="证券代码；省略则查全市场")] = None,
                start_date: DateStr = None, end_date: DateStr = None,
                limit: Annotated[int, Field(ge=1, le=500)] = 100) -> list[dict]:
    """限售解禁（含未来计划）。按代码查该股全部批次，或按日期区间查全市场（默认今天起 30 天）。
    解禁数量单位股，解禁市值单位元（按公告时股价），比例 %。"""
    t = S.lockup
    stmt = select(t, StockInfo.name).outerjoin(StockInfo, StockInfo.code == t.c.code)
    if code:
        stmt = _range(stmt.where(t.c.code == _code(code)), t.c.free_date, start_date, end_date)
        stmt = stmt.order_by(t.c.free_date.desc())
    else:
        s = _date(start_date, "start_date") or date.today()
        e = _date(end_date, "end_date") or s + timedelta(days=30)
        stmt = stmt.where(t.c.free_date >= s, t.c.free_date <= e).order_by(t.c.free_date, t.c.market_cap.desc())
    return _dicts(db.execute(stmt.limit(limit)).all())


# ---------------------------------------------------------------- 交易数据


def get_margin_trading(db: Session, code: Code, start_date: DateStr = None, end_date: DateStr = None,
                       limit: Limit = 250) -> dict:
    """融资融券个股明细（按日期正序，2010 年起）：融资余额 / 买入 / 偿还、融券余额（元），
    融券余量 / 卖出量（股），两融余额（元）。"""
    c = _code(code)
    t = S.margin_trading
    cols = ["rzye", "rzmre", "rzche", "rqye", "rqyl", "rqmcl", "rzrqye"]
    stmt = _range(select(t.c.trade_date, *(t.c[x] for x in cols)).where(t.c.code == c),
                  t.c.trade_date, start_date, end_date)
    rows = db.execute(stmt.order_by(t.c.trade_date.desc()).limit(limit)).all()
    return {"code": c, **_series(rows, ["date", *cols])}


def _code_or_day(db: Session, table, code: str | None, trade_date: str | None, limit: int):  # noqa: ANN001
    if not code and not trade_date:
        raise UserError("code 和 trade_date 至少给一个")
    stmt = select(table, StockInfo.name).outerjoin(StockInfo, StockInfo.code == table.c.code)
    if code:
        stmt = stmt.where(table.c.code == _code(code))
    if trade_date:
        stmt = stmt.where(table.c.trade_date == _date(trade_date, "trade_date"))
    return _dicts(db.execute(stmt.order_by(table.c.trade_date.desc()).limit(limit)).all())


def get_billboard(db: Session, code: Annotated[str | None, Field(description="证券代码")] = None,
                  trade_date: DateStr = None, limit: Annotated[int, Field(ge=1, le=500)] = 100) -> list[dict]:
    """龙虎榜（2004 年起）：按个股查历次上榜，或查某日全部上榜（code / trade_date 至少给一个）。
    金额单位元，deal_ratio 为龙虎榜成交占总成交 %。"""
    return _code_or_day(db, S.billboard, code, trade_date, limit)


def get_block_trades(db: Session, code: Annotated[str | None, Field(description="证券代码")] = None,
                     trade_date: DateStr = None, limit: Annotated[int, Field(ge=1, le=500)] = 100) -> list[dict]:
    """大宗交易（2000 年起）：按个股或按日期查（至少给一个）。价格元，premium_pct 相对收盘溢价 %，
    数量股，金额元，附买卖营业部。"""
    return _code_or_day(db, S.block_trade, code, trade_date, limit)


# ---------------------------------------------------------------- 公告、快讯、宏观


def get_announcements(db: Session, code: Code, start_date: DateStr = None, end_date: DateStr = None,
                      limit: Annotated[int, Field(ge=1, le=200)] = 30) -> list[dict]:
    """上市公司公告列表（按公告日倒序）：标题、类别、公告日、原文链接；已生成 AI 摘要的附摘要。"""
    c = _code(code)
    t = S.announcement
    stmt = _range(select(t.c.art_code, t.c.title, t.c.category, t.c.ann_date, t.c.summary).where(t.c.code == c),
                  t.c.ann_date, start_date, end_date)
    rows = _dicts(db.execute(stmt.order_by(t.c.ann_date.desc()).limit(limit)).all())
    for r in rows:
        r["url"] = f"https://data.eastmoney.com/notices/detail/{c}/{r['art_code']}.html"
        if not r["summary"]:
            r.pop("summary")
    return rows


NEWS_EXCERPT = 200  # 快讯正文来自第三方媒体，只给摘录并附原文链接


def get_news(db: Session, view: Literal["featured", "all"] = "featured",
             query: Annotated[str | None, Field(max_length=50, description="关键词（只搜近 30 天）")] = None,
             limit: Annotated[int, Field(ge=1, le=50)] = 20) -> list[dict]:
    """全球财经快讯（多家媒体合并成事件，按时间倒序）。featured 只看要闻（LLM 1-5 分重要度 + 多家报道），
    all 看全部。正文为摘录，完整内容见各来源链接。"""
    from app.services import news

    out = []
    for e in news.list_events(db, limit=limit, view=view, q=(query or "").strip() or None):
        content = e["content"] or ""
        out.append({
            "id": e["id"], "pub_at": e["pub_at"], "title": e["title"],
            "excerpt": content[:NEWS_EXCERPT] + ("…" if len(content) > NEWS_EXCERPT else ""),
            "score": e["score"], "category": e["category"], "sectors": e["sectors"], "stocks": e["stocks"],
            "sources": [{"name": s["name"], "url": s["url"]} for s in e["sources"]],
            "followups": len(e.get("followups") or []),
        })
    return out


def list_macro_indicators(db: Session) -> list[dict]:
    """宏观与利率指标目录：key、名称、单位、频率（D/M/Q）及最新一期值。"""
    t = S.macro_series
    out = []
    for key, (name, unit, freq, *_rest) in MACRO.items():
        last = db.execute(select(t.c.period, t.c.value).where(t.c.indicator == key)
                          .order_by(t.c.period.desc()).limit(1)).first()
        out.append({"key": key, "name": name, "unit": unit, "freq": freq,
                    "period": _v(last[0]) if last else None, "value": last[1] if last else None})
    return out


def get_macro_series(db: Session, indicator: Annotated[str, Field(description="指标 key，见 list_macro_indicators")],
                     start_date: DateStr = None, end_date: DateStr = None, limit: Limit = 240) -> dict:
    """宏观 / 利率序列（按所属期正序）。月度取当月 1 日、季度取季末月 1 日、日度取当日。"""
    if indicator not in MACRO:
        raise UserError(f"未知指标 {indicator!r}，可用：{', '.join(MACRO)}")
    t = S.macro_series
    stmt = _range(select(t.c.period, t.c.value).where(t.c.indicator == indicator), t.c.period, start_date, end_date)
    rows = db.execute(stmt.order_by(t.c.period.desc()).limit(limit)).all()
    name, unit, freq, *_rest = MACRO[indicator]
    return {"key": indicator, "name": name, "unit": unit, "freq": freq, **_series(rows, ["period", "value"])}


TOOLS = [
    search_securities, get_stock_profile, get_realtime_quotes, get_kline, list_indices, get_index_bars,
    get_index_constituents, list_industries,
    get_daily_basic, get_market_daily_basic,
    get_financial_indicators, get_financial_statements, get_dividends, get_share_capital, get_top_holders,
    get_holder_count, get_lockups,
    get_margin_trading, get_billboard, get_block_trades,
    get_announcements, get_news, list_macro_indicators, get_macro_series,
]
