"""采集与写库：股票池、财报、分红、宏观利率、指数成分、公告、退市股日 K。

每个函数接收 SQLAlchemy 会话，拉取数据源后批量 upsert 并提交；重复执行幂等。
不依赖 Kairos，命令行入口见 kairos_data.cli。
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, insert, select

from kairos_data.schema import (announcement, dividend, fin_report, index_constituent, kline,
                                macro_series, stock_info)
from kairos_data.sources import baostock, eastmoney_dc
from kairos_data.store import upsert_rows

logger = logging.getLogger("kairos_data")

_A_SHARE = '(SECURITY_TYPE_CODE="058001001")'

# indicator → (名称, 单位, 频率, 报表, 日期列, 数值列, 过滤)
MACRO: dict[str, tuple[str, str, str, str, str, str, str]] = {
    "gdp_yoy": ("GDP 累计同比", "%", "Q", "RPT_ECONOMY_GDP", "REPORT_DATE", "SUM_SAME", ""),
    "cpi_yoy": ("CPI 同比", "%", "M", "RPT_ECONOMY_CPI", "REPORT_DATE", "NATIONAL_SAME", ""),
    "ppi_yoy": ("PPI 同比", "%", "M", "RPT_ECONOMY_PPI", "REPORT_DATE", "BASE_SAME", ""),
    "pmi_mfg": ("制造业 PMI", "", "M", "RPT_ECONOMY_PMI", "REPORT_DATE", "MAKE_INDEX", ""),
    "pmi_non_mfg": ("非制造业 PMI", "", "M", "RPT_ECONOMY_PMI", "REPORT_DATE", "NMAKE_INDEX", ""),
    "m2_yoy": ("M2 同比", "%", "M", "RPT_ECONOMY_CURRENCY_SUPPLY", "REPORT_DATE", "BASIC_CURRENCY_SAME", ""),
    "m1_yoy": ("M1 同比", "%", "M", "RPT_ECONOMY_CURRENCY_SUPPLY", "REPORT_DATE", "CURRENCY_SAME", ""),
    "retail_yoy": ("社零同比", "%", "M", "RPT_ECONOMY_TOTAL_RETAIL", "REPORT_DATE", "RETAIL_TOTAL_SAME", ""),
    "lpr_1y": ("LPR 1 年", "%", "D", "RPTA_WEB_RATE", "TRADE_DATE", "LPR1Y", ""),
    "lpr_5y": ("LPR 5 年以上", "%", "D", "RPTA_WEB_RATE", "TRADE_DATE", "LPR5Y", ""),
    "shibor_on": ("Shibor 隔夜", "%", "D", "RPT_IMP_INTRESTRATEN", "REPORT_DATE", "IR_RATE",
                  '(MARKET_CODE="001")(CURRENCY_CODE="CNY")(INDICATOR_ID="001")'),
    "shibor_1w": ("Shibor 1 周", "%", "D", "RPT_IMP_INTRESTRATEN", "REPORT_DATE", "IR_RATE",
                  '(MARKET_CODE="001")(CURRENCY_CODE="CNY")(INDICATOR_ID="101")'),
    "shibor_3m": ("Shibor 3 月", "%", "D", "RPT_IMP_INTRESTRATEN", "REPORT_DATE", "IR_RATE",
                  '(MARKET_CODE="001")(CURRENCY_CODE="CNY")(INDICATOR_ID="203")'),
    "shibor_1y": ("Shibor 1 年", "%", "D", "RPT_IMP_INTRESTRATEN", "REPORT_DATE", "IR_RATE",
                  '(MARKET_CODE="001")(CURRENCY_CODE="CNY")(INDICATOR_ID="301")'),
    "cgb_2y": ("中国国债 2 年", "%", "D", "RPTA_WEB_TREASURYYIELD", "SOLAR_DATE", "EMM00588704", ""),
    "cgb_5y": ("中国国债 5 年", "%", "D", "RPTA_WEB_TREASURYYIELD", "SOLAR_DATE", "EMM00166462", ""),
    "cgb_10y": ("中国国债 10 年", "%", "D", "RPTA_WEB_TREASURYYIELD", "SOLAR_DATE", "EMM00166466", ""),
    "cgb_30y": ("中国国债 30 年", "%", "D", "RPTA_WEB_TREASURYYIELD", "SOLAR_DATE", "EMM00166469", ""),
    "ust_2y": ("美国国债 2 年", "%", "D", "RPTA_WEB_TREASURYYIELD", "SOLAR_DATE", "EMG00001306", ""),
    "ust_10y": ("美国国债 10 年", "%", "D", "RPTA_WEB_TREASURYYIELD", "SOLAR_DATE", "EMG00001310", ""),
}


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s[:10]) if s else None


def _f(x) -> float | None:  # noqa: ANN001
    try:
        return None if x is None or x == "" else float(x)
    except (TypeError, ValueError):
        return None


def _today_cst() -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).date()


def quarter_ends(n: int, today: date | None = None) -> list[date]:
    """today 之前（含）最近 n 个季末，倒序。"""
    d = today or _today_cst()
    q_month = (d.month - 1) // 3 * 3  # 上一个季末所在月；0 表示去年 12 月
    y, m = (d.year, q_month) if q_month else (d.year - 1, 12)
    out = []
    while len(out) < n:
        out.append(date(y, m, 31 if m in (3, 12) else 30))
        y, m = (y, m - 3) if m > 3 else (y - 1, 12)
    return out


def _period_filter(col: str, day: date) -> str:
    return f"({col}='{day.isoformat()}')"


# ---------------------------------------------------------------- 财报

def fin_rows(raw: list[dict]) -> list[dict]:
    now = datetime.now(timezone.utc)
    rows = {}
    for r in raw:
        key = (r["SECURITY_CODE"], _d(r["REPORTDATE"]))
        rows[key] = {
            "code": key[0], "report_date": key[1],
            "notice_date": _d(r.get("NOTICE_DATE")) or _d(r.get("UPDATE_DATE")),
            "eps": _f(r.get("BASIC_EPS")), "deduct_eps": _f(r.get("DEDUCT_BASIC_EPS")),
            "revenue": _f(r.get("TOTAL_OPERATE_INCOME")), "net_profit": _f(r.get("PARENT_NETPROFIT")),
            "revenue_yoy": _f(r.get("YSTZ")), "profit_yoy": _f(r.get("SJLTZ")),
            "roe": _f(r.get("WEIGHTAVG_ROE")), "bps": _f(r.get("BPS")),
            "ocf_ps": _f(r.get("MGJYXJJE")), "gross_margin": _f(r.get("XSMLL")),
            "dividend_plan": (r.get("ASSIGNDSCRPT") or None) and r["ASSIGNDSCRPT"][:128],
            "updated_at": now,
        }
    return list(rows.values())


def refresh_financials(db, periods: list[date]) -> int:
    n = 0
    for p in periods:
        rows = fin_rows(eastmoney_dc.fetch_report(
            "RPT_LICO_FN_CPD", _period_filter("REPORTDATE", p) + _A_SHARE))
        upsert_rows(db, fin_report, rows)
        db.commit()
        n += len(rows)
    return n


# ---------------------------------------------------------------- 分红

def dividend_rows(raw: list[dict]) -> list[dict]:
    rows = {}
    for r in raw:
        key = (r["SECURITY_CODE"], _d(r["REPORT_DATE"]))
        rows[key] = {
            "code": key[0], "report_date": key[1],
            "plan_notice_date": _d(r.get("PLAN_NOTICE_DATE")), "notice_date": _d(r.get("NOTICE_DATE")),
            "record_date": _d(r.get("EQUITY_RECORD_DATE")), "ex_date": _d(r.get("EX_DIVIDEND_DATE")),
            "cash_per10": _f(r.get("PRETAX_BONUS_RMB")), "bonus_per10": _f(r.get("BONUS_RATIO")),
            "transfer_per10": _f(r.get("IT_RATIO")),
            "progress": (r.get("ASSIGN_PROGRESS") or "")[:32],
            "plan": (r.get("IMPL_PLAN_PROFILE") or "")[:128],
        }
    return list(rows.values())


def refresh_dividends(db, periods: list[date]) -> int:
    n = 0
    for p in periods:
        rows = dividend_rows(eastmoney_dc.fetch_report(
            "RPT_SHAREBONUS_DET", _period_filter("REPORT_DATE", p)))
        upsert_rows(db, dividend, rows)
        db.commit()
        n += len(rows)
    return n


# ---------------------------------------------------------------- 宏观

def macro_rows(indicator: str, raw: list[dict]) -> list[dict]:
    _name, _unit, _freq, _report, date_col, value_col, _flt = MACRO[indicator]
    rows = {}
    for r in raw:
        v, p = _f(r.get(value_col)), _d(r.get(date_col))
        if v is not None and p is not None:
            rows[p] = {"indicator": indicator, "period": p, "value": v}
    return list(rows.values())


def refresh_macro(db, full: bool = False) -> int:
    # 同一报表（如国债收益率的 6 个期限）只拉一次
    cache: dict[tuple[str, str, str], list[dict]] = {}
    n = 0
    for ind, (_n, _u, _f2, report, date_col, _v, flt) in MACRO.items():
        key = (report, date_col, flt)
        if key not in cache:
            cache[key] = eastmoney_dc.fetch_report(report, flt, sort=date_col,
                                                     max_pages=None if full else 1)
        rows = macro_rows(ind, cache[key])
        upsert_rows(db, macro_series, rows)
        db.commit()
        n += len(rows)
    return n


# ---------------------------------------------------------------- 指数成分

def refresh_index_constituents(db, days: list[date]) -> int:
    """按给定日期取成分；同一调整日的名单只写一次。"""
    have = set(db.execute(
        select(index_constituent.c.index_code, index_constituent.c.snapshot_date).distinct()
    ).tuples())
    n = 0
    with baostock.session() as bs:
        for index_code in baostock.INDEX_QUERIES:
            for day in days:
                members = baostock.fetch_index_members(bs, index_code, day.isoformat())
                if not members:
                    continue
                snap = _d(members[0]["updateDate"])
                if (index_code, snap) in have:
                    continue
                have.add((index_code, snap))
                rows = [{"index_code": index_code, "snapshot_date": snap,
                         "code": m["code"].split(".", 1)[1]} for m in members]
                upsert_rows(db, index_constituent, rows)
                db.commit()
                n += len(rows)
    return n


def month_starts(since: date, until: date) -> list[date]:
    out, d = [], date(since.year, since.month, 1)
    while d <= until:
        out.append(d)
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


# ---------------------------------------------------------------- 公告

def announcement_rows(items: list[dict], code: str | None = None) -> list[dict]:
    rows = {}
    for it in items:
        codes = [c.get("stock_code") for c in it.get("codes") or [] if c.get("stock_code")]
        if not codes or not it.get("art_code"):
            continue
        cols = it.get("columns") or []
        rows[it["art_code"]] = {
            "art_code": it["art_code"],
            "code": code if code in codes else codes[0],
            "title": (it.get("title") or "")[:512],
            "category": (cols[0].get("column_name") or "")[:64] if cols else "",
            "ann_date": _d(it.get("notice_date")) or _d(it.get("display_time")),
        }
    return [r for r in rows.values() if r["ann_date"]]


def refresh_announcements(db, max_pages: int = 20) -> int:
    """拉最新公告，直到翻到库里最新日期的前一天为止（首次最多 max_pages 页）。"""
    latest = db.execute(select(func.max(announcement.c.ann_date))).scalar()
    stop = latest - timedelta(days=1) if latest else None
    n = 0
    for page in range(1, max_pages + 1):
        items, _total = eastmoney_dc.fetch_announcements(page)
        rows = announcement_rows(items)
        upsert_rows(db, announcement, rows)
        db.commit()
        n += len(rows)
        if not items or (stop and rows and min(r["ann_date"] for r in rows) < stop):
            break
    return n


# ---------------------------------------------------------------- 股票池

def _market(code: str, market: str) -> str:
    # 北交所代码段 4xxxxx / 8xxxxx / 92xxxx；腾讯源的股票池里有一部分带 sz 前缀
    return "BJ" if code.startswith(("4", "8", "92")) else market


def sync_listing(db, raw: list[dict[str, str]], add_listed: bool = True) -> int:
    """raw 为 BaoStock query_stock_basic 的行；返回写入行数。

    已在表里的股票只补上市 / 退市日期，名称和行业不动；表里没有的退市股整行写入。
    add_listed=False 时表里没有的在市股票也不写（Kairos 由行情源维护在市股票池）。
    """
    # 只要 A 股（type=1），B 股 200xxx / 900xxx 不进股票池
    basics = {}
    for r in raw:
        exchange, code = r["code"].split(".", 1)
        if r["type"] == "1" and not code.startswith(("2", "9")):
            basics[code] = (exchange.upper(), r)
    if not basics:
        logger.warning("listing: baostock returned nothing, skip")
        return 0

    rows = []
    known = set()
    for s in db.execute(select(stock_info)).mappings():
        known.add(s["code"])
        _ex, r = basics.get(s["code"], (None, None))
        rows.append({
            "code": s["code"], "name": s["name"], "market": _market(s["code"], s["market"]),
            "industry": s["industry"],
            "list_date": _d(r["ipoDate"]) if r else s["list_date"],
            "delist_date": _d(r["outDate"]) if r else s["delist_date"],
        })
    added = 0
    for code, (ex, r) in basics.items():
        if code in known or (r["status"] == "1" and not add_listed):
            continue
        rows.append({
            "code": code, "name": r["code_name"].strip(), "market": ex, "industry": "—",
            "list_date": _d(r["ipoDate"]), "delist_date": _d(r["outDate"]),
        })
        added += 1
    upsert_rows(db, stock_info, rows)
    db.commit()
    logger.info("listing: %d rows, %d added", len(rows), added)
    return len(rows)


def refresh_listing(db, add_listed: bool = True) -> int:
    return sync_listing(db, baostock.fetch_security_basics(), add_listed=add_listed)


# ---------------------------------------------------------------- 退市股日 K

def delisted_bars(raw: list[dict[str, str]]) -> list[dict]:
    """BaoStock 行 → kline 行：只要交易日，成交量股 → 手（与腾讯口径一致）。"""
    out = []
    for r in raw:
        if r.get("tradestatus") != "1" or not r.get("close"):
            continue
        out.append({
            "period": "1d", "ts": datetime.fromisoformat(r["date"]),
            "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]),
            "close": float(r["close"]), "volume": int(float(r["volume"] or 0)) // 100,
        })
    return out


def backfill_delisted(db) -> tuple[int, int, int]:
    """退市股日 K 用 BaoStock 前复权全历史整段替换（腾讯拿不到已退市股票）。

    退市后不再除权，序列定型；BaoStock 比库里根数多才替换，可断点续跑。北交所跳过。
    返回 (替换, 跳过, 失败)。
    """
    codes = list(db.execute(
        select(stock_info.c.code, stock_info.c.market)
        .where(stock_info.c.delist_date.is_not(None), stock_info.c.market.in_(("SH", "SZ")))
        .order_by(stock_info.c.code)
    ).tuples())
    have = dict(db.execute(
        select(kline.c.code, func.count()).where(kline.c.period == "1d").group_by(kline.c.code)
    ).all())
    replaced = skipped = failed = 0
    with baostock.session() as bs:
        for i, (code, market) in enumerate(codes, 1):
            symbol = f"{market.lower()}.{code}"
            try:
                try:
                    rows = delisted_bars(baostock.fetch_daily_qfq(bs, symbol))
                except RuntimeError as exc:
                    # BaoStock 匿名会话服务端共享：别的进程 logout 后这里也会掉线，重登一次
                    if "未登录" not in str(exc):
                        raise
                    bs.login()
                    rows = delisted_bars(baostock.fetch_daily_qfq(bs, symbol))
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.warning("%s 拉取失败：%s", code, exc)
                continue
            if len(rows) <= have.get(code, 0):
                skipped += 1
                continue
            db.execute(delete(kline).where(kline.c.code == code, kline.c.period == "1d"))
            db.execute(insert(kline), [{"code": code, **r} for r in rows])
            db.commit()
            replaced += 1
            if i % 20 == 0:
                logger.info("退市股日 K %d/%d：替换 %d，跳过 %d，失败 %d",
                            i, len(codes), replaced, skipped, failed)
            time.sleep(0.05)
    logger.info("退市股日 K 完成：替换 %d，跳过 %d，失败 %d（共 %d 只）",
                replaced, skipped, failed, len(codes))
    return replaced, skipped, failed


# ---------------------------------------------------------------- 组合入口

PARTS = ("fin", "div", "macro", "index")


def _run(db, label: str, fn) -> None:  # noqa: ANN001
    try:
        logger.info("%s: %d rows", label, fn())
    except Exception:  # noqa: BLE001 — 单项数据源失败不拖累其他项
        logger.exception("%s failed", label)
        db.rollback()


def daily(db) -> None:
    """日更：财报最近 2 个报告期、分红最近 4 个报告期、宏观各序列最新一页、指数成分当日名单。"""
    _run(db, "fin", lambda: refresh_financials(db, quarter_ends(2)))
    _run(db, "dividend", lambda: refresh_dividends(db, quarter_ends(4)))
    _run(db, "macro", lambda: refresh_macro(db))
    _run(db, "index", lambda: refresh_index_constituents(db, [_today_cst()]))


def backfill(db, parts: tuple[str, ...] = PARTS) -> None:
    """全量回补：财报 1988 年起、分红 1990 年起、宏观全历史、指数成分 2005 年起逐月。"""
    today = _today_cst()
    if "fin" in parts:
        periods = quarter_ends((today.year - 1988) * 4 + 4)
        _run(db, "fin backfill", lambda: refresh_financials(db, periods))
    if "div" in parts:
        periods = quarter_ends((today.year - 1990) * 4 + 4)
        _run(db, "dividend backfill", lambda: refresh_dividends(db, periods))
    if "macro" in parts:
        _run(db, "macro backfill", lambda: refresh_macro(db, full=True))
    if "index" in parts:
        days = month_starts(date(2005, 1, 1), today)
        _run(db, "index backfill", lambda: refresh_index_constituents(db, days))


def missing_parts(db) -> tuple[str, ...]:
    tables = {"fin": fin_report, "div": dividend, "macro": macro_series, "index": index_constituent}
    return tuple(k for k, t in tables.items()
                 if not db.execute(select(func.count()).select_from(t)).scalar())
