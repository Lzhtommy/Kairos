"""采集与写库：股票池、日 K（在市补到上市日 / 退市股全历史）、每日指标、财报、分红、股本股东、宏观利率、指数成分、公告。

每个函数接收 SQLAlchemy 会话，拉取数据源后批量 upsert 并提交；重复执行幂等。
不依赖 Kairos，命令行入口见 kairos_data.cli。
"""

from __future__ import annotations

import logging
import time
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, insert, select

from kairos_data.schema import (STATEMENT_ITEMS, announcement, billboard, block_trade, daily_basic, dividend,
                                fin_report, fin_report_revision, fin_statement, holder_count, index_bar, index_constituent,
                                kline, lockup, macro_series, margin_trading, security_bar, security_info,
                                share_capital, stock_info, sync_state, top_holder)
from kairos_data.sources import baostock, csindex, eastmoney_dc
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
    "indus_yoy": ("工业增加值同比", "%", "M", "RPT_ECONOMY_INDUS_GROW", "REPORT_DATE", "BASE_SAME", ""),
    "fai_yoy": ("固定资产投资当月同比", "%", "M", "RPT_ECONOMY_ASSET_INVEST", "REPORT_DATE", "BASE_SAME", ""),
    "export_yoy": ("出口同比", "%", "M", "RPT_ECONOMY_CUSTOMS", "REPORT_DATE", "EXIT_BASE_SAME", ""),
    "import_yoy": ("进口同比", "%", "M", "RPT_ECONOMY_CUSTOMS", "REPORT_DATE", "IMPORT_BASE_SAME", ""),
    "new_loan": ("新增人民币贷款", "亿元", "M", "RPT_ECONOMY_RMB_LOAN", "REPORT_DATE", "RMB_LOAN", ""),
    "fx_reserve": ("外汇储备", "亿美元", "M", "RPT_ECONOMY_GOLD_CURRENCY", "REPORT_DATE", "FOREX", ""),
    # 按公告日记，值为调整后的大型金融机构存准率
    "rrr_large": ("存款准备金率（大型机构）", "%", "D", "RPT_ECONOMY_DEPOSIT_RESERVE", "REPORT_DATE",
                  "INTEREST_RATE_BA", ""),
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
    "margin_rzye": ("两融·融资余额", "元", "D", "RPTA_RZRQ_LSHJ", "DIM_DATE", "RZYE", ""),
    "margin_rqye": ("两融·融券余额", "元", "D", "RPTA_RZRQ_LSHJ", "DIM_DATE", "RQYE", ""),
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


_REVISED_FIELDS = ("eps", "deduct_eps", "revenue", "net_profit", "roe", "bps")


def _changed(old: float | None, new: float | None) -> bool:
    if old is None or new is None:
        return old is not None  # 原有值被清空算更正；补上缺值不算
    return abs(old - new) > max(abs(old), abs(new)) * 1e-6


def fin_revisions(have: dict[str, dict], rows: list[dict], now: datetime) -> list[dict]:
    """新抓的行与库里同期旧行比，核心数值变了的返回旧版本（写进 fin_report_revision）。"""
    out = []
    for r in rows:
        old = have.get(r["code"])
        if old and any(_changed(old[f], r[f]) for f in _REVISED_FIELDS):
            out.append({"code": r["code"], "report_date": r["report_date"], "revised_at": now,
                        "notice_date": old["notice_date"], **{f: old[f] for f in _REVISED_FIELDS}})
    return out


def refresh_financials(db, periods: list[date]) -> int:
    n = 0
    for p in periods:
        rows = fin_rows(eastmoney_dc.fetch_report(
            "RPT_LICO_FN_CPD", _period_filter("REPORTDATE", p) + _A_SHARE, sort="SECURITY_CODE"))
        have = {r["code"]: r for r in db.execute(
            select(fin_report.c.code, fin_report.c.notice_date,
                   *(fin_report.c[f] for f in _REVISED_FIELDS)).where(fin_report.c.report_date == p)
        ).mappings()}
        if revised := fin_revisions(have, rows, datetime.now(timezone.utc)):
            logger.info("fin %s: %d 只财报数值有更正，旧版本已留档", p, len(revised))
            upsert_rows(db, fin_report_revision, revised)
        upsert_rows(db, fin_report, rows)
        db.commit()
        n += len(rows)
    return n


# ---------------------------------------------------------------- 三大报表主要科目

_STATEMENT_REPORTS = {"bs": "RPT_DMSK_FN_BALANCE", "is": "RPT_DMSK_FN_INCOME", "cf": "RPT_DMSK_FN_CASHFLOW"}


def statement_rows(raw_by_kind: dict[str, list[dict]]) -> list[dict]:
    """三张报表按 (代码, 报告期) 合成一行；某张表缺这只股票时对应列留空。"""
    rows: dict[tuple, dict] = {}
    for kind, raw in raw_by_kind.items():
        for r in raw:
            if r.get("SECURITY_TYPE_CODE") not in (None, "058001001") or not r.get("REPORT_DATE"):
                continue
            key = (r["SECURITY_CODE"], _d(r["REPORT_DATE"]))
            row = rows.setdefault(key, {"code": key[0], "report_date": key[1], "notice_date": None,
                                        **{c: None for c in STATEMENT_ITEMS}})
            for col, (k, field, _label) in STATEMENT_ITEMS.items():
                if k == kind:
                    row[col] = _f(r.get(field))
            if kind == "is" or row["notice_date"] is None:
                row["notice_date"] = _d(r.get("NOTICE_DATE")) or row["notice_date"]
    return list(rows.values())


def _have(db, col) -> set:  # noqa: ANN001
    return set(db.execute(select(col).distinct()).scalars())


def refresh_statements(db, periods: list[date]) -> int:
    n = 0
    for p in periods:
        raw = {kind: eastmoney_dc.fetch_report(report, _period_filter("REPORT_DATE", p), sort="SECUCODE")
               for kind, report in _STATEMENT_REPORTS.items()}
        rows = statement_rows(raw)
        upsert_rows(db, fin_statement, rows)
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


# ---------------------------------------------------------------- 交易类：两融、龙虎榜、大宗交易

def margin_rows(raw: list[dict]) -> list[dict]:
    rows = {}
    for r in raw:
        if not r.get("SCODE") or not r.get("DATE"):
            continue
        key = (r["SCODE"], _d(r["DATE"]))
        rows[key] = {"code": key[0], "trade_date": key[1],
                     **{c: _f(r.get(c.upper())) for c in
                        ("rzye", "rzmre", "rzche", "rqye", "rqyl", "rqmcl", "rzrqye")}}
    return list(rows.values())


def billboard_rows(raw: list[dict]) -> list[dict]:
    rows = {}
    for r in raw:
        if not _is_a(r) or not r.get("TRADE_DATE"):
            continue
        key = (r["SECURITY_CODE"], _d(r["TRADE_DATE"]), (r.get("EXPLANATION") or "")[:128])
        rows[key] = {
            "code": key[0], "trade_date": key[1], "reason": key[2],
            "close": _f(r.get("CLOSE_PRICE")), "change_pct": _f(r.get("CHANGE_RATE")),
            "turnover_rate": _f(r.get("TURNOVERRATE")), "buy_amt": _f(r.get("BILLBOARD_BUY_AMT")),
            "sell_amt": _f(r.get("BILLBOARD_SELL_AMT")), "net_amt": _f(r.get("BILLBOARD_NET_AMT")),
            "deal_amt": _f(r.get("BILLBOARD_DEAL_AMT")), "deal_ratio": _f(r.get("DEAL_AMOUNT_RATIO")),
            "comment": (r.get("EXPLAIN") or "")[:256],
        }
    return list(rows.values())


def block_trade_rows(raw: list[dict]) -> list[dict]:
    return [
        {"code": r["SECURITY_CODE"], "trade_date": _d(r["TRADE_DATE"]), "price": _f(r.get("DEAL_PRICE")),
         "premium_pct": None if _f(r.get("PREMIUM_RATIO")) is None else _f(r["PREMIUM_RATIO"]) * 100,
         "volume": _i(r.get("DEAL_VOLUME")),
         "amount": _f(r.get("DEAL_AMT")), "buyer": (r.get("BUYER_NAME") or "")[:128],
         "seller": (r.get("SELLER_NAME") or "")[:128]}
        for r in raw
        if r.get("SECURITY_TYPE") == "EQA" and _is_a(r) and r.get("TRADE_DATE")  # 去掉基金、债券
    ]


def refresh_margin(db, days: list[date]) -> int:
    n = 0
    for day in days:
        rows = margin_rows(eastmoney_dc.fetch_report(
            "RPTA_WEB_RZRQ_GGMX", f"(DATE='{day.isoformat()}')", sort="SCODE"))
        upsert_rows(db, margin_trading, rows)
        db.commit()
        n += len(rows)
    return n


def refresh_billboard(db, months: list[tuple[date, date]]) -> int:
    n = 0
    for start, end in months:
        rows = billboard_rows(eastmoney_dc.fetch_report(
            "RPT_DAILYBILLBOARD_DETAILSNEW", _range_filter("TRADE_DATE", start, end),
            sort="TRADE_DATE,SECUCODE,EXPLANATION"))
        upsert_rows(db, billboard, rows)
        db.commit()
        n += len(rows)
    return n


def refresh_block_trade(db, months: list[tuple[date, date]]) -> int:
    n = 0
    for start, end in months:
        rows = block_trade_rows(eastmoney_dc.fetch_report(
            "RPT_DATA_BLOCKTRADE", _range_filter("TRADE_DATE", start, end),
            sort="TRADE_DATE,SECUCODE,DEAL_AMT,BUYER_NAME,SELLER_NAME"))
        db.execute(delete(block_trade).where(block_trade.c.trade_date.between(start, end)))
        if rows:
            db.execute(insert(block_trade), rows)
        db.commit()
        n += len(rows)
    return n


def _days(since: date, until: date) -> list[date]:
    """since 到 until 的工作日，倒序（节假日接口返回空，代价一次请求）。"""
    out, d = [], until
    while d >= since:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out


# ---------------------------------------------------------------- ETF 与可转债

def etf_rows(raw: list[dict[str, str]]) -> list[dict]:
    """BaoStock 证券列表里的 ETF（type=5）。"""
    out = []
    for r in raw:
        if r.get("type") != "5":
            continue
        exchange, code = r["code"].split(".", 1)
        out.append({"code": code, "exchange": exchange.upper(), "sec_type": "etf",
                    "name": r["code_name"].strip()[:64], "list_date": _d(r["ipoDate"]),
                    "delist_date": _d(r["outDate"])})
    return out


def cb_rows(raw: list[dict]) -> list[dict]:
    """东财可转债列表；还没上市的（申购阶段）没有上市日，也收录。"""
    out = []
    for r in raw:
        suffix = (r.get("SECUCODE") or "").rpartition(".")[2]
        if suffix not in ("SH", "SZ"):
            continue
        out.append({
            "code": r["SECURITY_CODE"], "exchange": suffix, "sec_type": "cb",
            "name": (r.get("SECURITY_NAME_ABBR") or "")[:64], "list_date": _d(r.get("LISTING_DATE")),
            "delist_date": _d(r.get("DELIST_DATE")), "stock_code": r.get("CONVERT_STOCK_CODE"),
            "rating": (r.get("RATING") or None) and r["RATING"][:16],
            "issue_size": _f(r.get("ACTUAL_ISSUE_SCALE")), "maturity_date": _d(r.get("EXPIRE_DATE")),
            "convert_price": _f(r.get("INITIAL_TRANSFER_PRICE")),
        })
    return out


def refresh_securities(db) -> int:
    """ETF 与可转债清单。两类列名不同，分开 upsert。"""
    etfs = etf_rows(baostock.fetch_security_basics())
    cbs = cb_rows(eastmoney_dc.fetch_report("RPT_BOND_CB_LIST", sort="SECURITY_CODE"))
    upsert_rows(db, security_info, etfs)
    upsert_rows(db, security_info, cbs)
    db.commit()
    return len(etfs) + len(cbs)


def _secid(exchange: str, code: str) -> str:
    return f"{1 if exchange == 'SH' else 0}.{code}"


def backfill_security_bars(db, throttle: float = 0.1) -> tuple[int, int]:
    """ETF 与可转债日线全历史，每只一次请求；已有日线的跳过（日更由快照接上）。返回 (写入, 失败)。"""
    have = set(db.execute(select(security_bar.c.code).distinct()).scalars())
    todo = db.execute(
        select(security_info.c.code, security_info.c.exchange)
        .where(security_info.c.list_date.is_not(None)).order_by(security_info.c.code)
    ).tuples().all()
    done = failed = 0
    for i, (code, exchange) in enumerate(todo, 1):
        if code in have:
            continue
        try:
            raw = eastmoney_dc.fetch_his_kline(_secid(exchange, code))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.warning("%s 日线取数失败：%s", code, exc)
            continue
        rows = [{"code": code, "trade_date": _d(r["date"]), "open": r["open"], "high": r["high"],
                 "low": r["low"], "close": r["close"], "volume": r["volume"], "amount": r["amount"]}
                for r in raw]
        upsert_rows(db, security_bar, rows)
        db.commit()
        done += 1
        if i % 200 == 0:
            logger.info("ETF / 转债日线 %d/%d：写入 %d，失败 %d", i, len(todo), done, failed)
        time.sleep(throttle)
    return done, failed


def snapshot_security_rows(raw: list[dict], today: date) -> list[dict]:
    """收盘快照 → 当日日线：只要更新时间落在 today、且当天有成交的。"""
    out = []
    for r in raw:
        if not isinstance(r.get("close"), (int, float)) or not isinstance(r.get("open"), (int, float)):
            continue  # 停牌 / 未上市时字段是 "-"
        ts = r.get("ts")
        if not ts or (datetime.fromtimestamp(ts, timezone.utc) + timedelta(hours=8)).date() != today:
            continue
        out.append({"code": r["code"], "trade_date": today, "open": r["open"], "high": r["high"],
                    "low": r["low"], "close": r["close"], "volume": r["volume"], "amount": r["amount"]})
    return out


def refresh_security_snapshot(db) -> int:
    today = _today_cst()
    n = 0
    for fs in eastmoney_dc.SNAPSHOT_FS.values():
        rows = snapshot_security_rows(eastmoney_dc.fetch_snapshot(fs), today)
        upsert_rows(db, security_bar, rows)
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


# 指数代码 → (名称, 东财 secid, 中证官网有无成分权重)
INDEXES: dict[str, tuple[str, str, bool]] = {
    "000001": ("上证指数", "1.000001", False),
    "399001": ("深证成指", "0.399001", False),
    "399006": ("创业板指", "0.399006", False),
    "000016": ("上证 50", "1.000016", True),
    "000300": ("沪深 300", "1.000300", True),
    "000905": ("中证 500", "1.000905", True),
    "000852": ("中证 1000", "1.000852", True),
    "932000": ("中证 2000", "2.932000", True),
    "000688": ("科创 50", "1.000688", True),
    "899050": ("北证 50", "0.899050", False),
    "000985": ("中证全指", "1.000985", False),
}


def index_bar_rows(index_code: str, raw: list[dict]) -> list[dict]:
    return [{"index_code": index_code, "trade_date": _d(r["date"]), "open": r["open"], "high": r["high"],
             "low": r["low"], "close": r["close"], "volume": r.get("volume"), "amount": r.get("amount")}
            for r in raw]


def refresh_index_bars(db) -> int:
    """主要指数日线，每次取全历史整段 upsert（每个指数一次请求）。"""
    n = 0
    for code, (_name, secid, _csi) in INDEXES.items():
        rows = index_bar_rows(code, eastmoney_dc.fetch_his_kline(secid))
        upsert_rows(db, index_bar, rows)
        db.commit()
        n += len(rows)
    return n


def refresh_index_weights(db) -> int:
    """中证官网当期成分与权重，按其快照日写入 index_constituent。"""
    n = 0
    for code, (_name, _secid, csi) in INDEXES.items():
        if not csi:
            continue
        rows = [{"index_code": code, "snapshot_date": w["date"], "code": w["code"], "weight": w["weight"]}
                for w in csindex.fetch_weights(code)]
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
    newer = [{k: v for k, v in h.items() if k != "code"} for h in have if h["ts"] > last]
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


# ---------------------------------------------------------------- 每日指标（ST / 停牌 / 换手 / 估值）

_BS_EPOCH = date(1990, 12, 19)  # BaoStock 日线最早一天


def _bs_retry(bs, fn, *args):  # noqa: ANN001, ANN202
    """BaoStock 匿名会话服务端共享：别的进程 logout 后这里也会掉线，重登一次。"""
    try:
        return fn(bs, *args)
    except RuntimeError as exc:
        if "未登录" not in str(exc):
            raise
        bs.login()
        return fn(bs, *args)


def share_history(db) -> dict[str, list[tuple[date, int]]]:
    """每只股票的 (变动日, 总股本) 升序序列，用于按日期取当时的总股本。"""
    out: dict[str, list[tuple[date, int]]] = {}
    for code, day, total in db.execute(
        select(share_capital.c.code, share_capital.c.change_date, share_capital.c.total_shares)
        .where(share_capital.c.total_shares.is_not(None))
        .order_by(share_capital.c.code, share_capital.c.change_date)
    ).tuples():
        out.setdefault(code, []).append((day, total))
    return out


def daily_basic_rows(code: str, raw: list[dict[str, str]], shares: list[tuple[date, int]]) -> list[dict]:
    """BaoStock 每日指标行 → daily_basic 行；总市值按当日适用的总股本算，早于第一条股本记录时为空。"""
    days = [d for d, _ in shares]
    out = []
    for r in raw:
        day = _d(r["date"])
        close = _f(r["close"])
        i = bisect_right(days, day)
        total = shares[i - 1][1] if i else None
        out.append({
            "code": code, "trade_date": day, "close": close, "preclose": _f(r["preclose"]),
            "volume": _f(r["volume"]), "amount": _f(r["amount"]), "turn": _f(r["turn"]),
            "suspended": r["tradestatus"] == "0", "is_st": r["isST"] == "1",
            "pe_ttm": _f(r["peTTM"]), "pb_mrq": _f(r["pbMRQ"]), "ps_ttm": _f(r["psTTM"]),
            "pcf_ttm": _f(r["pcfNcfTTM"]),
            "total_mv": close * total if close and total else None,
        })
    return out


def _sync_daily_basic(db, bs, code: str, market: str, start: date, end: date,  # noqa: ANN001
                      shares: dict[str, list[tuple[date, int]]]) -> int:
    raw = _bs_retry(bs, baostock.fetch_daily_basic, f"{market.lower()}.{code}",
                    start.isoformat(), end.isoformat())
    rows = daily_basic_rows(code, raw, shares.get(code, []))
    upsert_rows(db, daily_basic, rows)
    db.commit()
    return len(rows)


def daily_basic_gaps(db) -> list[tuple[str, str, date]]:
    """沪深股票（含退市）里每日指标没补到上市日的 (code, market, 起始日)。"""
    first = (select(daily_basic.c.code, func.min(daily_basic.c.trade_date).label("first"))
             .group_by(daily_basic.c.code).subquery())
    out = []
    for code, market, listed, first_day in db.execute(
        select(stock_info.c.code, stock_info.c.market, stock_info.c.list_date, first.c.first)
        .outerjoin(first, first.c.code == stock_info.c.code)
        .where(stock_info.c.market.in_(("SH", "SZ")))
        .order_by(stock_info.c.code)
    ).tuples():
        start = max(listed or _BS_EPOCH, _BS_EPOCH)
        if first_day is None or first_day > start + _LISTING_SLACK:
            out.append((code, market, start))
    return out


def backfill_daily_basic(db, throttle: float = 0.05) -> tuple[int, int]:
    """每日指标补到上市日（BaoStock 单只全历史 2~15 秒，全市场数小时）。返回 (完成, 失败)。

    每只股票一次提交，已补齐的不在候选里，中断后重跑即可续上。
    """
    todo = daily_basic_gaps(db)
    logger.info("每日指标待补到上市日：%d 只", len(todo))
    shares = share_history(db)
    end = _today_cst()
    done = failed = 0
    with baostock.session() as bs:
        for i, (code, market, start) in enumerate(todo, 1):
            try:
                _sync_daily_basic(db, bs, code, market, start, end, shares)
                done += 1
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                failed += 1
                logger.warning("%s 每日指标拉取失败：%s", code, exc)
            if i % 100 == 0:
                logger.info("每日指标补历史 %d/%d：完成 %d，失败 %d", i, len(todo), done, failed)
            time.sleep(throttle)
    logger.info("每日指标补历史完成：完成 %d，失败 %d（共 %d 只）", done, failed, len(todo))
    return done, failed


def refresh_daily_basic(db, lookback: int = 10) -> int:
    """增量：在市沪深股票从库里最后一天的次日拉到今天；库里没有的只拉最近 lookback 天
    （全历史交给 backfill_daily_basic）。BaoStock 当日数据 17:30 后才入库，需在此之后跑。"""
    today = _today_cst()
    last = dict(db.execute(
        select(daily_basic.c.code, func.max(daily_basic.c.trade_date)).group_by(daily_basic.c.code)
    ).all())
    shares = share_history(db)
    total = 0
    with baostock.session() as bs:
        for code, market, listed in db.execute(
            select(stock_info.c.code, stock_info.c.market, stock_info.c.list_date)
            .where(stock_info.c.delist_date.is_(None), stock_info.c.market.in_(("SH", "SZ")))
            .order_by(stock_info.c.code)
        ).tuples():
            have = last.get(code)
            start = have + timedelta(days=1) if have else max(listed or today, today - timedelta(days=lookback))
            if start > today:
                continue
            try:
                total += _sync_daily_basic(db, bs, code, market, start, today, shares)
            except Exception as exc:  # noqa: BLE001 — 单只失败下次增量会补上
                db.rollback()
                logger.warning("%s 每日指标增量失败：%s", code, exc)
    return total


# ---------------------------------------------------------------- 组合入口

PARTS = ("fin", "stmt", "div", "macro", "index", "indexbar", "capital", "holdernum", "holders",
         "lockup", "margin", "billboard", "block", "securities")


def _run(db, label: str, fn) -> bool:  # noqa: ANN001
    try:
        logger.info("%s: %d rows", label, fn())
        return True
    except Exception:  # noqa: BLE001 — 单项数据源失败不拖累其他项
        logger.exception("%s failed", label)
        db.rollback()
        return False


def daily(db) -> None:
    """日更：财报与三大报表最近 2 个报告期、分红最近 4 个报告期、宏观各序列最新一页、指数成分当日名单、
    近 40 天的股本变动和股东户数、最近 2 期十大股东、近 30 天起的解禁（含未来计划）、
    近 7 天的两融 / 龙虎榜 / 大宗交易、主要指数日线与中证成分权重、ETF 与可转债清单和当日快照、
    每日指标（ST / 停牌 / 换手 / 估值）。
    需在收盘后（建议 18:00 后）跑，快照才是当日收盘。"""
    _run(db, "fin", lambda: refresh_financials(db, quarter_ends(2)))
    _run(db, "stmt", lambda: refresh_statements(db, quarter_ends(2)))
    _run(db, "dividend", lambda: refresh_dividends(db, quarter_ends(4)))
    _run(db, "macro", lambda: refresh_macro(db))
    _run(db, "index", lambda: refresh_index_constituents(db, [_today_cst()]))
    _run(db, "index weight", lambda: refresh_index_weights(db))
    _run(db, "index bar", lambda: refresh_index_bars(db))
    today = _today_cst()
    recent = month_ranges(today - timedelta(days=40), today)
    _run(db, "capital", lambda: refresh_share_capital(db, recent))
    _run(db, "holdernum", lambda: refresh_holder_count(db, recent))
    _run(db, "holders", lambda: refresh_top_holders(db, quarter_ends(2)))
    _run(db, "lockup", lambda: refresh_lockup(db, today - timedelta(days=30)))
    _run(db, "margin", lambda: refresh_margin(db, _days(today - timedelta(days=7), today)))
    _run(db, "billboard", lambda: refresh_billboard(db, month_ranges(today - timedelta(days=7), today)))
    _run(db, "block", lambda: refresh_block_trade(db, month_ranges(today - timedelta(days=7), today)))
    _run(db, "securities", lambda: refresh_securities(db))
    _run(db, "security snapshot", lambda: refresh_security_snapshot(db))
    _run(db, "daily basic", lambda: refresh_daily_basic(db))


def backfill(db, parts: tuple[str, ...] = PARTS) -> None:
    """全量回补：财报与三大报表 1988 年起、分红 1990 年起、宏观全历史、指数成分 2005 年起逐月、
    股本变动 1990 年起、股东户数 2010 年起（东财实际从 2013 年才有）、十大股东 1990 年起、解禁全部、两融 2010 年起（约 4000
    个交易日，最慢，约 2 小时）、龙虎榜 2004 年起、大宗交易 2000 年起、主要指数全历史、
    ETF 与可转债清单及全历史日线（约 2700 只，每只一次请求）。"""
    today = _today_cst()
    if "fin" in parts:
        periods = quarter_ends((today.year - 1988) * 4 + 4)
        if _run(db, "fin backfill", lambda: refresh_financials(db, periods)):
            _mark_done(db, "fin")
    if "stmt" in parts:
        have = _have(db, fin_statement.c.report_date)  # 每期一次提交，已有的期次是完整的
        periods = [p for p in quarter_ends((today.year - 1988) * 4 + 4) if p not in have]
        if _run(db, "stmt backfill", lambda: refresh_statements(db, periods)):
            _mark_done(db, "stmt")
    if "div" in parts:
        periods = quarter_ends((today.year - 1990) * 4 + 4)
        if _run(db, "dividend backfill", lambda: refresh_dividends(db, periods)):
            _mark_done(db, "div")
    if "macro" in parts:
        if _run(db, "macro backfill", lambda: refresh_macro(db, full=True)):
            _mark_done(db, "macro")
    if "index" in parts:
        days = month_starts(date(2005, 1, 1), today)
        ok = _run(db, "index backfill", lambda: refresh_index_constituents(db, days))
        if _run(db, "index weight", lambda: refresh_index_weights(db)) and ok:
            _mark_done(db, "index")
    if "indexbar" in parts:
        if _run(db, "index bar backfill", lambda: refresh_index_bars(db)):
            _mark_done(db, "indexbar")
    if "capital" in parts:
        months = month_ranges(date(1990, 1, 1), today)
        if _run(db, "capital backfill", lambda: refresh_share_capital(db, months)):
            _mark_done(db, "capital")
    if "holdernum" in parts:
        months = month_ranges(date(2010, 1, 1), today)
        if _run(db, "holdernum backfill", lambda: refresh_holder_count(db, months)):
            _mark_done(db, "holdernum")
    if "holders" in parts:
        # 流通股东先写、全部股东后写，后者有了这期才完整
        have = set(db.execute(select(top_holder.c.end_date).where(top_holder.c.kind == "all")
                              .distinct()).scalars())
        periods = [p for p in quarter_ends((today.year - 1990) * 4 + 4) if p not in have]
        if _run(db, "holders backfill", lambda: refresh_top_holders(db, periods)):
            _mark_done(db, "holders")
    if "lockup" in parts:
        if _run(db, "lockup backfill", lambda: refresh_lockup(db)):
            _mark_done(db, "lockup")
    if "margin" in parts:
        have = _have(db, margin_trading.c.trade_date)
        days = [d for d in _days(date(2010, 3, 31), today) if d not in have]
        if _run(db, "margin backfill", lambda: refresh_margin(db, days)):
            _mark_done(db, "margin")
    if "billboard" in parts:
        months = month_ranges(date(2004, 6, 1), today)
        if _run(db, "billboard backfill", lambda: refresh_billboard(db, months)):
            _mark_done(db, "billboard")
    if "block" in parts:
        months = month_ranges(date(2000, 8, 1), today)
        if _run(db, "block backfill", lambda: refresh_block_trade(db, months)):
            _mark_done(db, "block")
    if "securities" in parts:
        _run(db, "securities", lambda: refresh_securities(db))
        if _run(db, "security bar backfill", lambda: backfill_security_bars(db)[0]):
            _mark_done(db, "securities")


def _mark_done(db, part: str) -> None:
    upsert_rows(db, sync_state, [{"key": f"backfill:{part}", "value": "done",
                                  "updated_at": datetime.now(timezone.utc)}])
    db.commit()


def missing_parts(db) -> tuple[str, ...]:
    """还没完成全量回补的部分：以 sync_state 的完成标记为准。完成标记上线前就已补完的
    几项（LEGACY_DONE）没有标记，表非空即视为完成。"""
    done = {k.removeprefix("backfill:") for k in db.execute(select(sync_state.c.key)).scalars()}
    tables = {"fin": fin_report, "stmt": fin_statement, "div": dividend, "macro": macro_series,
              "index": index_constituent, "indexbar": index_bar,
              "capital": share_capital, "holdernum": holder_count, "holders": top_holder,
              "lockup": lockup, "margin": margin_trading, "billboard": billboard,
              "block": block_trade, "securities": security_bar}
    return tuple(k for k, t in tables.items()
                 if k not in done and (k not in LEGACY_DONE
                                       or not db.execute(select(func.count()).select_from(t)).scalar()))


# 完成标记上线（2026-11-09）前已在线上补完的部分
LEGACY_DONE = frozenset({"fin", "div", "macro", "index", "capital", "holdernum"})
