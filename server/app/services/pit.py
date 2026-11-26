"""某天当时已公告的 ROE(TTM) 与近 12 个月股息率——回测选股与研报估值共用。

这两项不落 daily_basic：全历史物化要改写上千万行，而由 fin_report / dividend 现算只需按需读几万行。
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.warehouse import Dividend, FinReport


class PitFundamentals:
    """ROE：取公告日 ≤ 当天的最新一期；年报直接用，季报按"上年年报 + 本期累计 − 上年同期累计"
    滚成 TTM（加权 ROE 近似可加），缺上年数据时按累计值年化。股息率 = 除权日在近 365 天内的
    每股现金分红 / 当天不复权收盘价（小数，0.03 = 3%）。

    codes 给定时只读这些股票（两张表主键都以 code 开头，走主键范围扫描）。
    """

    def __init__(self, db: Session, start: date, end: date, codes: list[str] | None = None) -> None:
        reports = (
            select(FinReport.code, FinReport.notice_date, FinReport.report_date, FinReport.roe)
            .where(FinReport.notice_date.is_not(None), FinReport.notice_date <= end,
                   FinReport.report_date >= date(start.year - 2, 1, 1))
        )
        cash = (
            select(Dividend.code, Dividend.ex_date, Dividend.cash_per10)
            .where(Dividend.ex_date.is_not(None), Dividend.ex_date <= end,
                   Dividend.ex_date > start - timedelta(days=366), Dividend.cash_per10 > 0)
        )
        if codes is not None:
            reports = reports.where(FinReport.code.in_(codes))
            cash = cash.where(Dividend.code.in_(codes))
        self.reports: dict[str, list[tuple[date, date, float | None]]] = {}
        for code, notice, period, roe in db.execute(reports):
            self.reports.setdefault(code, []).append((notice, period, roe))
        self.cash: dict[str, list[tuple[date, float]]] = {}
        for code, ex, per10 in db.execute(cash):
            self.cash.setdefault(code, []).append((ex, per10 / 10))

    def covers(self, code: str) -> bool:
        return code in self.reports

    def roe(self, code: str, day: date) -> float | None:
        known = {period: roe for notice, period, roe in self.reports.get(code, ()) if notice <= day}
        if not known:
            return None
        latest = max(known)
        cur = known[latest]
        if cur is None or latest.month == 12:
            return cur
        annual = known.get(date(latest.year - 1, 12, 31))
        same = known.get(latest.replace(year=latest.year - 1))
        if annual is not None and same is not None:
            return round(annual + cur - same, 2)
        return round(cur * 12 / latest.month, 2)

    def dividend_yield(self, code: str, day: date, price: float | None) -> float | None:
        if not price or price <= 0:
            return None
        cash = sum(c for ex, c in self.cash.get(code, ()) if day - timedelta(days=365) < ex <= day)
        return round(cash / price, 4)

    def apply_roe(self, rows: list[dict], day: date) -> None:
        """有财报数据的股票换成当时的 ROE（当时尚无公告则缺值，不拿今天的冒充）。"""
        for r in rows:
            if self.covers(r["code"]):
                r["roe"] = self.roe(r["code"], day)
