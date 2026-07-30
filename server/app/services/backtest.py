"""Simplified vectorized backtest over daily K-lines.

Selects the DSL-passing universe, holds it equal-weight, rebalances monthly
(applying two-sided cost), and reports annualized return / max drawdown /
Sharpe / win-rate / hit count plus a downsampled equity curve.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from statistics import mean, pstdev
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.market import Kline
from app.services.strategy_exec import run_dsl

TRADING_DAYS = 252


def _closes_by_code(db: Session, codes: list[str]) -> dict[str, list[tuple[datetime, float]]]:
    out: dict[str, list[tuple[datetime, float]]] = {}
    rows = db.execute(
        select(Kline.code, Kline.ts, Kline.close)
        .where(Kline.code.in_(codes), Kline.period == "1d")
        .order_by(Kline.ts.asc())
    ).all()
    for code, ts, close in rows:
        out.setdefault(code, []).append((ts, close))
    return out


def _empty_result(hit_count: int = 0) -> dict[str, Any]:
    return {
        "metrics": {"annualizedReturn": 0.0, "maxDrawdown": 0.0, "sharpe": 0.0,
                    "winRate": 0.0, "hitCount": hit_count},
        "curve": [],
    }


def run(db: Session, dsl: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    passing = run_dsl(db, dsl)
    hit_count = len(passing)

    series = _closes_by_code(db, [r["code"] for r in passing])
    # 新上市 / 数据缺口的股票没有 K 线，跳过（此前 close_map[c] 直接 KeyError）
    codes = [r["code"] for r in passing if series.get(r["code"])]
    if not codes:
        return _empty_result(hit_count)

    # common trading calendar = dates of the constituent with the longest history
    calendar = [ts for ts, _ in max(series.values(), key=len)]
    close_map = {c: {ts: v for ts, v in s} for c, s in series.items()}

    rate = float(params.get("cost", {}).get("rate", dsl.get("cost", {}).get("rate", 0.0005)))

    daily_returns: list[float] = []
    dates: list[datetime] = []
    for i in range(1, len(calendar)):
        d0, d1 = calendar[i - 1], calendar[i]
        rets = []
        for c in codes:
            p0 = close_map[c].get(d0)
            p1 = close_map[c].get(d1)
            if p0 and p1 and p0 > 0:
                rets.append(p1 / p0 - 1)
        if not rets:
            continue
        r = mean(rets)
        # monthly rebalance cost on the first trading day of a new month
        if d1.month != d0.month:
            r -= 2 * rate
        daily_returns.append(r)
        dates.append(d1)

    # equity curve
    equity = 1.0
    curve: list[dict[str, Any]] = [{"t": calendar[0].strftime("%Y-%m-%d"), "v": 1.0}]
    peak = 1.0
    max_dd = 0.0
    for r, d in zip(daily_returns, dates):
        equity *= 1 + r
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1)
        curve.append({"t": d.strftime("%Y-%m-%d"), "v": round(equity, 4)})

    n = len(daily_returns)
    annualized = (equity ** (TRADING_DAYS / n) - 1) if n else 0.0
    vol = pstdev(daily_returns) if n > 1 else 0.0
    sharpe = (mean(daily_returns) / vol * (TRADING_DAYS ** 0.5)) if vol else 0.0
    win_rate = (sum(1 for r in daily_returns if r > 0) / n) if n else 0.0

    # downsample curve to ~120 points
    if len(curve) > 120:
        step = len(curve) // 120
        curve = curve[::step] + [curve[-1]]

    return {
        "metrics": {
            "annualizedReturn": round(annualized * 100, 2),
            "maxDrawdown": round(max_dd * 100, 2),
            "sharpe": round(sharpe, 2),
            "winRate": round(win_rate * 100, 1),
            "hitCount": hit_count,
        },
        "curve": curve,
    }


def write_curve(backtest_id: int, curve: list[dict[str, Any]]) -> str:
    os.makedirs(settings.reports_dir, exist_ok=True)
    path = os.path.join(settings.reports_dir, f"backtest-{backtest_id}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(curve, fh)
    return path
