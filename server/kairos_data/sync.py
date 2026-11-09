"""采集与写库：股票池、日 K（在市补到上市日 / 退市股全历史）、财报、分红、股本股东、宏观利率、指数成分、公告。

每个函数接收 SQLAlchemy 会话，拉取数据源后批量 upsert 并提交；重复执行幂等。
不依赖 Kairos，命令行入口见 kairos_data.cli。
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, insert, select

from kairos_data.schema import (announcement, dividend, fin_report, holder_count, index_constituent,
                                kline, lockup, macro_series, share_capital, stock_info, top_holder)
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
            "RPT_LICO_FN_CPD", _period_filter("REPORTDATE", p) + _A_SHARE, sort="SECURITY_CODE"))
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
            "RPT_SHAREBONUS_DET", _period_filter("REPORT_DATE", p), sort="SECURITY_CODE"))
        upsert_rows(db, dividend, rows)
        db.commit()
        n += len(rows)
    return n


# ---------------------------------------------------------------- 股本、股东、解禁

def _is_a(r: dict) -> bool:
    """沪深京 A 股：排除新三板（.NQ）和 B 股（沪 900、深 200 开头）。"""
    suffix = (r.get("SECUCODE") or "").rpartition(".")[2]
    code = r.get("SECURITY_CODE") or ""
    return suffix == "BJ" or (suffix in ("SH", "SZ") and not code.startswith(("2", "9")))


def _i(x) -> int | None:  # noqa: ANN001
    v = _f(x)
    return None if v is None else int(round(v))


def month_ranges(since: date, until: date) -> list[tuple[date, date]]:
    """[since, until] 按自然月切成闭区间，倒序（最近的先拉）。"""
    out = []
    for start in month_starts(since, until):
        nxt = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
        out.append((start, min(nxt - timedelta(days=1), until)))
    return out[::-1]


def _range_filter(col: str, start: date, end: date) -> str:
    return f"({col}>='{start.isoformat()}')({col}<='{end.isoformat()}')"


def share_capital_rows(raw: list[dict]) -> list[dict]:
    rows = {}
    for r in raw:
        if not _is_a(r) or not r.get("END_DATE"):
            continue
        key = (r["SECURITY_CODE"], _d(r["END_DATE"]))
        rows[key] = {
            "code": key[0], "change_date": key[1], "notice_date": _d(r.get("NOTICE_DATE")),
            "total_shares": _i(r.get("TOTAL_SHARES")), "float_a_shares": _i(r.get("LISTED_A_SHARES")),
            "limited_shares": _i(r.get("LIMITED_SHARES")),
            "change_reason": (r.get("CHANGE_REASON") or "")[:64],
        }
    return list(rows.values())


def holder_count_rows(raw: list[dict]) -> list[dict]:
    rows = {}
    for r in raw:
        if not _is_a(r) or not r.get("END_DATE"):
            continue
        key = (r["SECURITY_CODE"], _d(r["END_DATE"]))
        rows[key] = {
            "code": key[0], "end_date": key[1], "notice_date": _d(r.get("HOLD_NOTICE_DATE")),
            "holder_num": _i(r.get("HOLDER_NUM")), "avg_hold_shares": _f(r.get("AVG_HOLD_NUM")),
            "total_a_shares": _i(r.get("TOTAL_A_SHARES")),
        }
    return list(rows.values())


def top_holder_rows(raw: list[dict], kind: str) -> list[dict]:
    ratio_col = "HOLD_NUM_RATIO" if kind == "all" else "FREE_HOLDNUM_RATIO"
    rows = {}
    for r in raw:
        if not _is_a(r) or not r.get("END_DATE") or r.get("HOLDER_RANK") is None:
            continue
        key = (r["SECURITY_CODE"], _d(r["END_DATE"]), kind, int(r["HOLDER_RANK"]))
        rows[key] = {
            "code": key[0], "end_date": key[1], "kind": kind, "rank": key[3],
            "holder_name": (r.get("HOLDER_NAME") or "")[:256],
            "holder_type": (r.get("HOLDER_TYPE") or "")[:32],
            "shares_type": (r.get("SHARES_TYPE") or "")[:64],
            "hold_num": _i(r.get("HOLD_NUM")), "hold_ratio": _f(r.get(ratio_col)),
            "change": str(r.get("HOLD_NUM_CHANGE") or "")[:32],
            "notice_date": _d(r.get("NOTICE_DATE")) or _d(r.get("UPDATE_DATE")),
        }
    return list(rows.values())


def lockup_rows(raw: list[dict]) -> list[dict]:
    def shares(x):  # noqa: ANN001, ANN202 — 接口单位万股 / 万元
        v = _f(x)
        return None if v is None else v * 1e4

    def pct(x):  # noqa: ANN001, ANN202 — 接口是比例
        v = _f(x)
        return None if v is None else v * 100

    rows = {}
    for r in raw:
        if not _is_a(r) or not r.get("FREE_DATE"):
            continue
        key = (r["SECURITY_CODE"], _d(r["FREE_DATE"]), (r.get("FREE_SHARES_TYPE") or "")[:64])
        rows[key] = {
            "code": key[0], "free_date": key[1], "shares_type": key[2],
            "holder_num": _i(r.get("BATCH_HOLDER_NUM")),
            "free_shares": shares(r.get("CURRENT_FREE_SHARES")),
            "able_free_shares": shares(r.get("ABLE_FREE_SHARES")),
            "market_cap": shares(r.get("LIFT_MARKET_CAP")),
            "float_ratio": pct(r.get("FREE_RATIO")), "total_ratio": pct(r.get("TOTAL_RATIO")),
        }
    return list(rows.values())


def refresh_share_capital(db, months: list[tuple[date, date]]) -> int:
    n = 0
    for start, end in months:
        rows = share_capital_rows(eastmoney_dc.fetch_report(
            "RPT_F10_EH_EQUITY", _range_filter("END_DATE", start, end),
            sort="SECURITY_CODE,END_DATE,SECUCODE"))
        upsert_rows(db, share_capital, rows)
        db.commit()
        n += len(rows)
    return n


def refresh_holder_count(db, months: list[tuple[date, date]]) -> int:
    n = 0
    for start, end in months:
        rows = holder_count_rows(eastmoney_dc.fetch_report(
            "RPT_HOLDERNUM_DET", _range_filter("END_DATE", start, end),
            sort="SECURITY_CODE,END_DATE,SECUCODE"))
        upsert_rows(db, holder_count, rows)
        db.commit()
        n += len(rows)
    return n


def refresh_top_holders(db, periods: list[date]) -> int:
    """十大股东 + 十大流通股东。十大股东接口没有公告日，用同期十大流通股东的补上。"""
    n = 0
    for p in periods:
        by_kind = {
            kind: top_holder_rows(eastmoney_dc.fetch_report(
                report, _period_filter("END_DATE", p), sort="SECUCODE,HOLDER_RANK"), kind)
            for kind, report in (("float", "RPT_F10_EH_FREEHOLDERS"), ("all", "RPT_F10_EH_HOLDERS"))
        }
        notice = {r["code"]: r["notice_date"] for r in by_kind["float"] if r["notice_date"]}
        for r in by_kind["all"]:
            r["notice_date"] = r["notice_date"] or notice.get(r["code"])
        for rows in by_kind.values():
            upsert_rows(db, top_holder, rows)
            db.commit()
            n += len(rows)
    return n


def refresh_lockup(db, since: date | None = None) -> int:
    """since 之后（含）的解禁，含未来的解禁计划；None 为全部历史。"""
    flt = f"(FREE_DATE>='{since.isoformat()}')" if since else ""
    rows = lockup_rows(eastmoney_dc.fetch_report("RPT_LIFT_STAGE", flt,
                                                 sort="SECUCODE,FREE_DATE,FREE_SHARES_TYPE"))
    upsert_rows(db, lockup, rows)
    db.commit()
    return len(rows)


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
    """BaoStock 日 K 行 → kline 行：只要交易日，成交量股 → 手（与腾讯口径一致）。"""
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


def _baostock_bars(bs, symbol: str) -> list[dict]:  # noqa: ANN001
    try:
        return delisted_bars(baostock.fetch_daily_qfq(bs, symbol))
    except RuntimeError as exc:
        # BaoStock 匿名会话服务端共享：别的进程 logout 后这里也会掉线，重登一次
        if "未登录" not in str(exc):
            raise
        bs.login()
        return delisted_bars(baostock.fetch_daily_qfq(bs, symbol))


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
                rows = _baostock_bars(bs, symbol)
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


# ---------------------------------------------------------------- 在市股票日 K 补到上市日

# 最早一根 K 线晚于上市日这么多天才算缺历史（上市初停牌、数据源缺头几天都不算）
_LISTING_SLACK = timedelta(days=45)
_HISTORY_BARS = 20000  # 腾讯分页取数的上限，足够覆盖 1990 年至今
_TENCENT_COOLDOWN = 600  # 腾讯 ifzq 限流后改走 BaoStock 的秒数


def candle_rows(candles) -> list[dict]:  # noqa: ANN001
    return [{"period": "1d", "ts": c.ts, "open": c.open, "high": c.high, "low": c.low,
             "close": c.close, "volume": c.volume} for c in candles]


def replace_kline(db, code: str, rows: list[dict]) -> int:
    """整段替换一只股票的日 K，返回写入根数（0 = 未替换）。

    新数据明显比库里短（不到 90%）时不覆盖：腾讯对北交所只回一根，整段替换会清空历史。
    库里比新数据最后一根更晚的 bar 保留（BaoStock 盘后还没有当日 bar，采集已写入）。
    """
    if not rows:
        return 0
    have = db.execute(select(kline).where(kline.c.code == code, kline.c.period == "1d")).mappings().all()
    if len(rows) < len(have) * 0.9:
        logger.warning("%s 只拿到 %d 根（库里 %d 根），跳过不覆盖", code, len(rows), len(have))
        return 0
    last = max(r["ts"] for r in rows)
    newer = [{k: v for k, v in h.items() if k not in ("id", "code")} for h in have if h["ts"] > last]
    db.execute(delete(kline).where(kline.c.code == code, kline.c.period == "1d"))
    db.execute(insert(kline), [{"code": code, **r} for r in rows + newer])
    db.commit()
    return len(rows) + len(newer)


def history_gaps(db) -> list[tuple[str, str]]:
    """在市沪深股票里日 K 没补到上市日的 (code, market)。北交所没有上市日，跳过。"""
    first = (select(kline.c.code, func.min(kline.c.ts).label("first"))
             .where(kline.c.period == "1d").group_by(kline.c.code).subquery())
    out = []
    for code, market, listed, first_ts in db.execute(
        select(stock_info.c.code, stock_info.c.market, stock_info.c.list_date, first.c.first)
        .outerjoin(first, first.c.code == stock_info.c.code)
        .where(stock_info.c.delist_date.is_(None), stock_info.c.list_date.is_not(None),
               stock_info.c.market.in_(("SH", "SZ")))
        .order_by(stock_info.c.code)
    ).tuples():
        if first_ts is None or first_ts.date() > listed + _LISTING_SLACK:
            out.append((code, market))
    return out


def backfill_history(db, tencent=None, throttle: float = 0.15) -> tuple[int, int, int]:  # noqa: ANN001
    """在市股票日 K 补到上市日：先走腾讯，限流后 10 分钟内改走 BaoStock。返回 (替换, 跳过, 失败)。

    已补齐的股票不在候选里，中断后重跑即可续上。
    """
    from contextlib import ExitStack

    from kairos_data.sources.tencent import TencentProvider

    tencent = tencent or TencentProvider()
    todo = history_gaps(db)
    logger.info("日 K 待补到上市日：%d 只", len(todo))
    replaced = skipped = failed = 0
    tencent_down_until = 0.0
    with ExitStack() as stack:
        bs = None
        for i, (code, market) in enumerate(todo, 1):
            rows = None
            if time.monotonic() >= tencent_down_until:
                try:
                    rows = candle_rows(tencent.get_kline_history(code, _HISTORY_BARS))
                    time.sleep(throttle)
                except Exception as exc:  # noqa: BLE001 — 限流 / 网络问题，切 BaoStock
                    logger.warning("%s 腾讯取数失败（%s），%d 秒内改用 BaoStock", code, exc, _TENCENT_COOLDOWN)
                    tencent_down_until = time.monotonic() + _TENCENT_COOLDOWN
            if rows is None:
                try:
                    if bs is None:
                        bs = stack.enter_context(baostock.session())
                    rows = _baostock_bars(bs, f"{market.lower()}.{code}")
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    logger.warning("%s BaoStock 取数失败：%s", code, exc)
                    continue
            if replace_kline(db, code, rows):
                replaced += 1
            else:
                skipped += 1
            if i % 100 == 0:
                logger.info("日 K 补历史 %d/%d：替换 %d，跳过 %d，失败 %d",
                            i, len(todo), replaced, skipped, failed)
    logger.info("日 K 补历史完成：替换 %d，跳过 %d，失败 %d（共 %d 只）",
                replaced, skipped, failed, len(todo))
    return replaced, skipped, failed


# ---------------------------------------------------------------- 组合入口

PARTS = ("fin", "div", "macro", "index", "capital", "holdernum", "holders", "lockup")


def _run(db, label: str, fn) -> None:  # noqa: ANN001
    try:
        logger.info("%s: %d rows", label, fn())
    except Exception:  # noqa: BLE001 — 单项数据源失败不拖累其他项
        logger.exception("%s failed", label)
        db.rollback()


def daily(db) -> None:
    """日更：财报最近 2 个报告期、分红最近 4 个报告期、宏观各序列最新一页、指数成分当日名单、
    近 40 天的股本变动和股东户数、最近 2 期十大股东、近 30 天起的解禁（含未来计划）。"""
    _run(db, "fin", lambda: refresh_financials(db, quarter_ends(2)))
    _run(db, "dividend", lambda: refresh_dividends(db, quarter_ends(4)))
    _run(db, "macro", lambda: refresh_macro(db))
    _run(db, "index", lambda: refresh_index_constituents(db, [_today_cst()]))
    today = _today_cst()
    recent = month_ranges(today - timedelta(days=40), today)
    _run(db, "capital", lambda: refresh_share_capital(db, recent))
    _run(db, "holdernum", lambda: refresh_holder_count(db, recent))
    _run(db, "holders", lambda: refresh_top_holders(db, quarter_ends(2)))
    _run(db, "lockup", lambda: refresh_lockup(db, today - timedelta(days=30)))


def backfill(db, parts: tuple[str, ...] = PARTS) -> None:
    """全量回补：财报 1988 年起、分红 1990 年起、宏观全历史、指数成分 2005 年起逐月、
    股本变动 1990 年起、股东户数 2010 年起、十大股东 1990 年起、解禁全部。十大股东最慢，约 1 小时。"""
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
    if "capital" in parts:
        months = month_ranges(date(1990, 1, 1), today)
        _run(db, "capital backfill", lambda: refresh_share_capital(db, months))
    if "holdernum" in parts:
        months = month_ranges(date(2010, 1, 1), today)
        _run(db, "holdernum backfill", lambda: refresh_holder_count(db, months))
    if "holders" in parts:
        periods = quarter_ends((today.year - 1990) * 4 + 4)
        _run(db, "holders backfill", lambda: refresh_top_holders(db, periods))
    if "lockup" in parts:
        _run(db, "lockup backfill", lambda: refresh_lockup(db))


def missing_parts(db) -> tuple[str, ...]:
    tables = {"fin": fin_report, "div": dividend, "macro": macro_series, "index": index_constituent,
              "capital": share_capital, "holdernum": holder_count, "holders": top_holder,
              "lockup": lockup}
    return tuple(k for k, t in tables.items()
                 if not db.execute(select(func.count()).select_from(t)).scalar())
