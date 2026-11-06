"""自选股：下单 CSV 解析、板块归类、开盘涨跌幅与列表组装。

导入格式即 a-stock-strategy 产出的 kf_order_<账户>_<YYYY-MM-DD>.csv（UTF-8 带 BOM）：
    算法类型,账户名称,算法实例,证券代码,任务数量,交易方向,开始时间,结束时间,...
只取 交易方向=买入 的行；交易日取 开始时间（20261030T092500000），缺失时退回文件名里的日期。
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.market import Kline, Quote
from app.services import hot_rank
from app.services.market import change_pct, stock_dicts

_DATE_IN_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})")


class OrderFileError(ValueError):
    pass


@dataclass
class ParsedOrders:
    trade_date: str
    # code → 合计买入数量（同一只股票拆成多行时累加），保持文件里首次出现的顺序
    buys: dict[str, int] = field(default_factory=dict)


def parse_order_csv(content: str, filename: str = "") -> ParsedOrders:
    reader = csv.DictReader(io.StringIO(content.lstrip("﻿")))
    fields = set(reader.fieldnames or [])
    missing = {"证券代码", "交易方向"} - fields
    if missing:
        raise OrderFileError(f"不是下单文件格式，缺少列：{'、'.join(sorted(missing))}")

    trade_date = ""
    buys: dict[str, int] = {}
    for row in reader:
        if (row.get("交易方向") or "").strip() != "买入":
            continue
        code = (row.get("证券代码") or "").strip()
        # Excel 另存可能吃掉前导 0（000002 → 2）
        if code.isdigit() and len(code) < 6:
            code = code.zfill(6)
        if not re.fullmatch(r"\d{6}", code):
            continue
        try:
            qty = int(float(row.get("任务数量") or 0))
        except ValueError:
            qty = 0
        buys[code] = buys.get(code, 0) + qty
        if not trade_date:
            start = (row.get("开始时间") or "").strip()
            if re.match(r"\d{8}", start):
                trade_date = f"{start[:4]}-{start[4:6]}-{start[6:8]}"

    if not buys:
        raise OrderFileError("文件里没有标记为买入的股票")
    if not trade_date:
        m = _DATE_IN_NAME.search(filename)
        trade_date = m.group(1) if m else ""
    if not trade_date:
        raise OrderFileError("无法确定交易日：开始时间与文件名里都没有日期")
    return ParsedOrders(trade_date=trade_date, buys=buys)


def board_of(code: str) -> str:
    """主板 / 创业板 / 科创板 / 北交所。"""
    if code.startswith(("300", "301", "302")):
        return "创业板"
    if code.startswith(("688", "689")):
        return "科创板"
    if code.startswith(("4", "8", "92")):
        return "北交所"
    return "主板"


def open_changes(db: Session, codes: list[str], trade_date: str) -> dict[str, float]:
    """交易日开盘价相对前一根日 K 收盘的涨跌幅（%）。

    当日 bar 由采集器用实时行情逐分钟覆盖，所以盘中也能取到当天开盘；
    数据源不带交易所时间戳（akshare）时采集器不写当日 bar，退回快照的今开 / 前收。
    交易日还没开盘（或停牌无 bar）的股票不出现在结果里。
    """
    if not codes:
        return {}
    day = datetime.fromisoformat(trade_date)
    rn = func.row_number().over(partition_by=Kline.code, order_by=Kline.ts.desc()).label("rn")
    sub = (
        select(Kline.code, Kline.ts, Kline.open, Kline.close, rn)
        .where(Kline.period == "1d", Kline.code.in_(codes), Kline.ts <= day)
        .subquery()
    )
    bars: dict[str, list] = {}
    for code, ts, o, c, _ in db.execute(select(sub).where(sub.c.rn <= 2).order_by(sub.c.rn)):
        bars.setdefault(code, []).append((ts, o, c))
    # 交易日就是快照所在日时，前收用交易所给的 prev_close：本地日 K 缺几天
    # （服务停机）时上一根 bar 不是真正的前一交易日
    live = {
        q.code: q
        for q in db.execute(select(Quote).where(Quote.code.in_(codes))).scalars()
        if q.prev_close and (q.ts + timedelta(hours=8)).date() == day.date()
    }
    out: dict[str, float] = {}
    for code, rows in bars.items():
        if rows[0][0].date() != day.date() or not rows[0][1]:
            continue
        prev = (live[code].prev_close if code in live else None) or (rows[1][2] if len(rows) > 1 else None)
        if prev:
            out[code] = change_pct(rows[0][1], prev)
    for code, q in live.items():
        if code not in out and q.open:
            out[code] = change_pct(q.open, q.prev_close)
    return out


# 交易日历参照股（与 paper._CAL_REF / backtest 兜底一致）
_CAL_REF = "600519"


def prev_trading_day(db: Session, trade_date: str) -> str:
    """交易日的前一个交易日：周一 → 上周五，节后首日 → 节前最后一天。

    日历取参照股的日 K（采集器会回补缺失 bar，生产上连续）；库里没有 K 线时
    退回到前一个工作日。
    """
    day = datetime.fromisoformat(trade_date)
    ts = db.execute(
        select(func.max(Kline.ts)).where(
            Kline.period == "1d", Kline.code == _CAL_REF, Kline.ts < day
        )
    ).scalar()
    if ts is not None:
        return ts.date().isoformat()
    d = day - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.date().isoformat()


def watchlist_rows(
    db: Session,
    buys: dict[str, int],
    trade_date: str,
    hot_ranks: dict[str, int | None],
    prev_hot_ranks: dict[str, int],
) -> list[dict[str, Any]]:
    codes = list(buys)
    quotes = {s["code"]: s for s in stock_dicts(db, codes)}
    opens = open_changes(db, codes, trade_date)
    out = []
    for code in codes:
        # 刚上市 / 已退市等没有快照的股票也要列出来，行情字段留空
        base = quotes.get(code) or _empty_quote(code)
        out.append(
            {
                **base,
                "board": board_of(code),
                "quantity": buys[code],
                "openPct": opens.get(code),
                "hotRank": hot_ranks.get(code),
                "hotRankPrev": prev_hot_ranks.get(code),
            }
        )
    return out


# 实时人气只能逐只查东财（单次 ~100ms、8 并发），命中太多时不查；
# 昨日人气由凌晨任务全市场落库，任意数量都直接读库
HOT_RANK_MAX = 500


def with_open_and_hot(db: Session, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """给选股结果补上今天的开盘涨跌幅与东财人气（今日实时 / 上一交易日），口径同自选股。"""
    codes = [s["code"] for s in items]
    day = hot_rank.today_cst()
    opens = open_changes(db, codes, day)
    today_ranks: dict[str, int | None] = {}
    prev_day = prev_trading_day(db, day)
    if 0 < len(codes) <= HOT_RANK_MAX:
        hot_rank.ensure_daily(db, codes, {prev_day})
        today_ranks = hot_rank.hot_ranks(codes)
    prev_ranks = hot_rank.daily_ranks(db, codes, prev_day)
    return [
        {
            **s,
            "openPct": opens.get(s["code"]),
            "hotRank": today_ranks.get(s["code"]),
            "hotRankPrev": prev_ranks.get(s["code"]),
        }
        for s in items
    ]


def _empty_quote(code: str) -> dict[str, Any]:
    zeros = ("price", "prevClose", "open", "high", "low", "volume", "turnover", "turnoverRate",
             "pb", "marketCap", "roe")
    return {"code": code, "name": code, "market": _market(code), "industry": "—", "pe": None,
            "spark": [], **dict.fromkeys(zeros, 0)}


def _market(code: str) -> str:
    if code.startswith(("4", "8", "92")):
        return "BJ"
    if code.startswith(("6", "9")):
        return "SH"
    return "SZ"
