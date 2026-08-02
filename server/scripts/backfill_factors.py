"""历史因子回补：用日 K 重构 + 东财季频 ROE，把 factor_history 填到过去 N 个交易日。

用法（在 server/ 下）：
    .venv/bin/python -m scripts.backfill_factors --days 500
    .venv/bin/python -m scripts.backfill_factors --days 500 --skip-roe-history

口径（与回测的 _reconstructed_rows 一致，另加两处修正）：
- 价格类因子（pe/pb/market_cap）按当日收盘相对今日的比例缩放（股本/盈利视为不变）
- 股息率 = 年度分红 / 价格 → 按价格反比缩放
- ROE 用东财 datacenter 季报（加权 ROE），按"报告期 + 45 天可见"的保守规则取值，
  取不到的（新股/停牌/接口缺失）退回当前值
- 已有快照的日期跳过（幂等），今日交易日留给收盘 cron 的 snapshot_factors
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timedelta

import httpx
from sqlalchemy import func, insert, select

from app.core.db import SessionLocal
from app.models.market import FactorSnapshot, Kline
from app.services.backtest import _reconstructed_rows
from app.services.market import factor_rows

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("backfill")

_ROE_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_DISCLOSE_LAG_DAYS = 45  # 报告期后多少天视为市场可见（保守近似）


def _quarter_ends(start: datetime, end: datetime) -> list[str]:
    out = []
    y = start.year
    while y <= end.year:
        for md in ("03-31", "06-30", "09-30", "12-31"):
            d = f"{y}-{md}"
            if start.strftime("%Y-%m-%d") <= d <= end.strftime("%Y-%m-%d"):
                out.append(d)
        y += 1
    return out


def fetch_roe_history(
    start: datetime, end: datetime, wanted: set[str] | None = None
) -> dict[str, dict[str, float]]:
    """code → {报告期 'YYYY-MM-DD': YTD 加权 ROE}。抓取失败只记日志，不中断回补。

    注意东财季报 ROE 是"年初至今"口径，与行情源的年化/TTM 不同——
    取值时在 _roe_asof 里拼成 TTM 再落库，保证与当前 quotes 的量纲一致。
    wanted 用于把结果裁剪到股票池内（东财季报含 B 股/北交所，全收会
    白占约一倍内存——生产 ECS 只有 896MB，省着点）。
    """
    out: dict[str, dict[str, float]] = {}
    client = httpx.Client(timeout=20, headers={"User-Agent": "Mozilla/5.0"}, trust_env=False)
    for report_date in _quarter_ends(start, end):
        page = 1
        try:
            while True:
                r = client.get(
                    _ROE_URL,
                    params={
                        "reportName": "RPT_LICO_FN_CPD",
                        "columns": "SECURITY_CODE,REPORTDATE,WEIGHTAVG_ROE",
                        "filter": f"(REPORTDATE='{report_date}')",
                        "pageSize": 500,
                        "pageNumber": page,
                    },
                )
                data = (r.json().get("result") or {})
                rows = data.get("data") or []
                for row in rows:
                    roe = row.get("WEIGHTAVG_ROE")
                    code = str(row["SECURITY_CODE"])
                    if roe is None or (wanted is not None and code not in wanted):
                        continue
                    out.setdefault(code, {})[report_date] = float(roe)
                if page >= int(data.get("pages") or 1):
                    break
                page += 1
                time.sleep(0.3)
            logger.info("ROE %s: 累计 %d 只", report_date, len(out))
        except Exception:  # noqa: BLE001
            logger.exception("ROE %s 抓取失败，跳过该期", report_date)
    return out


def _roe_asof(reports: dict[str, float] | None, day: datetime, fallback: float) -> float:
    """day 时点可见的 TTM ROE：最新可见 YTD + 上年年报 − 上年同期 YTD。

    缺上年数据时退化为按季度数年化（×4/q），再不行退回 fallback（当前值）。
    """
    if not reports:
        return fallback
    latest: str | None = None
    for rd in sorted(reports):
        visible = datetime.fromisoformat(rd) + timedelta(days=_DISCLOSE_LAG_DAYS)
        if visible <= day:
            latest = rd
    if latest is None:
        return fallback
    ytd = reports[latest]
    year, month = int(latest[:4]), int(latest[5:7])
    q = month // 3
    if q == 4:
        return round(ytd, 2)  # 年报即 TTM
    prev_annual = reports.get(f"{year - 1}-12-31")
    prev_same = reports.get(f"{year - 1}-{latest[5:]}")
    if prev_annual is not None and prev_same is not None:
        return round(ytd + prev_annual - prev_same, 2)
    return round(ytd * 4 / q, 2)


def _bars_on(db, day: datetime) -> dict[str, tuple[float, float]]:
    return {
        code: (close, vol)
        for code, close, vol in db.execute(
            select(Kline.code, Kline.close, Kline.volume).where(
                Kline.period == "1d", Kline.ts == day
            )
        )
    }


def main(days: int, with_roe_history: bool) -> None:
    db = SessionLocal()
    try:
        rows_today = factor_rows(db)
        if not rows_today:
            logger.error("quotes 为空，先让采集器跑起来")
            return
        cal = [
            r[0]
            for r in db.execute(
                select(Kline.ts)
                .where(Kline.period == "1d")
                .group_by(Kline.ts)
                .having(func.count() > 100)  # 剔除零星脏日期
                .order_by(Kline.ts.asc())
            )
        ]
        if len(cal) < 2:
            logger.error("K 线不足")
            return
        ref_date = cal[-1]
        window = cal[-(days + 1) : -1]  # 今日留给收盘 cron
        have = {
            r[0]
            for r in db.execute(select(FactorSnapshot.ts).distinct())
        }
        targets = [d for d in window if d not in have]
        logger.info(
            "参照日 %s；窗口 %d 天，待回补 %d 天（已有 %d）",
            ref_date.date(), len(window), len(targets), len(have),
        )
        if not targets:
            return

        roe_hist: dict[str, dict[str, float]] = {}
        if with_roe_history:
            # 多取 ~500 天：TTM 拼接需要上年年报与上年同期
            roe_hist = fetch_roe_history(
                targets[0] - timedelta(days=560),
                ref_date,
                wanted={r["code"] for r in rows_today},
            )
            logger.info("季频 ROE 覆盖 %d 只", len(roe_hist))

        ref_bars = _bars_on(db, ref_date)
        prev_cache: tuple[datetime, dict] | None = None
        done = 0
        for d in targets:
            idx = cal.index(d)
            prev_day = cal[idx - 1] if idx > 0 else None
            if prev_cache and prev_cache[0] == prev_day:
                prev_bars = prev_cache[1]
            else:
                prev_bars = _bars_on(db, prev_day) if prev_day else {}
            day_bars = _bars_on(db, d)
            rows = _reconstructed_rows(rows_today, ref_bars, day_bars, prev_bars)
            payload = []
            for r in rows:
                k = r["price"] / ref_bars[r["code"]][0] if ref_bars.get(r["code"]) else 1.0
                payload.append(
                    {
                        "code": r["code"],
                        "ts": d,
                        "pe": r["pe"],
                        "pb": r["pb"],
                        "roe": _roe_asof(roe_hist.get(r["code"]), d, r["roe"]),
                        "turnover_rate": r["turnover_rate"],
                        "turnover": r["turnover"],
                        "market_cap": r["market_cap"],
                        "change_pct": r["change_pct"],
                        "price": r["price"],
                        # 股息率 = 分红/价格，分红视为常量 → 按价格反比缩放
                        "dividend_yield": round(r["dividend_yield"] / k, 4) if k > 0 else r["dividend_yield"],
                        "industry": r["industry"],
                    }
                )
            if payload:
                db.execute(insert(FactorSnapshot), payload)
                db.commit()
            prev_cache = (d, day_bars)
            done += 1
            if done % 20 == 0 or done == len(targets):
                logger.info("回补进度 %d/%d（%s，%d 只）", done, len(targets), d.date(), len(payload))
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=500, help="回补最近 N 个交易日（默认 500）")
    ap.add_argument("--skip-roe-history", action="store_true", help="不拉东财季频 ROE（全部用当前值）")
    args = ap.parse_args()
    main(args.days, not args.skip_roe_history)
