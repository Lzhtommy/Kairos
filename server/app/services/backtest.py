"""Strategy backtest over daily K-lines — two modes, picked by the DSL shape.

事件驱动（含 technical 条件的策略）：对每只入围股票逐日滚动算信号，
信号次日入场，按持有期 / 反向信号 / 止盈止损退出，单股同时只持一笔；
统计事件数、胜率、平均收益、相对基准超额，曲线为事件的平均收益路径。

组合模式（纯标量策略）：等权或市值加权持有，按调仓频率扣双边成本，
输出年化 / 回撤 / 夏普 / 胜率与基准对比曲线。

已知简化（前端明示）：成分/因子取自当前快照（存在前视偏差——因子历史
落库后可消除）；组合模式不做逐期重选。所有参数服务端夹紧。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from statistics import mean, median, pstdev
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.market import FactorSnapshot, Kline, StockInfo
from app.services import technical
from app.services.dsl import apply_universe, execute, validate_dsl
from app.services.market import factor_rows

TRADING_DAYS = 252
_MAX_BARS = 760
_BENCHMARKS = {"000300": "沪深300", "000905": "中证500", "399006": "创业板指"}
_CHUNK = 400  # 每批加载 K 线的股票数，控制 ECS 内存峰值


def _clean_params(params: dict[str, Any], dsl: dict[str, Any]) -> dict[str, Any]:
    """夹紧所有用户参数到安全范围；非法枚举回退默认值。"""

    def pick(key: str, options: tuple, default: str) -> str:
        v = params.get(key)
        return v if v in options else default

    def num(key: str, default: float, lo: float, hi: float) -> float:
        try:
            return min(max(float(params.get(key, default)), lo), hi)
        except (TypeError, ValueError):
            return default

    return {
        "period": int(num("periodDays", 250, 60, _MAX_BARS)),
        "hold": int(num("holdDays", 10, 1, 60)),
        "entry": pick("entry", ("open", "close"), "open"),
        "exit": pick("exitRule", ("hold", "signal", "stop"), "hold"),
        "stop_gain": num("stopGain", 15.0, 1.0, 100.0),
        "stop_loss": num("stopLoss", 8.0, 1.0, 50.0),
        "rebalance": pick("rebalance", ("weekly", "monthly", "quarterly"), "monthly"),
        "rate": num("costRate", float((dsl.get("cost") or {}).get("rate", 0.0005)), 0.0, 0.003),
        "benchmark": pick("benchmark", tuple(_BENCHMARKS), "000300"),
        "weighting": pick("weighting", ("equal", "cap"), "equal"),
        "max_pos": int(num("maxPositions", 0, 0, 200)),
    }


def _bars_by_code(
    db: Session, codes: list[str], bars: int
) -> dict[str, list[tuple[datetime, float, float]]]:
    """每只股票最近 `bars` 根 (ts, open, close)，升序。"""
    if not codes:
        return {}
    rn = func.row_number().over(partition_by=Kline.code, order_by=Kline.ts.desc()).label("rn")
    sub = (
        select(Kline.code, Kline.ts, Kline.open, Kline.close, rn)
        .where(Kline.period == "1d", Kline.code.in_(codes))
        .subquery()
    )
    stmt = (
        select(sub.c.code, sub.c.ts, sub.c.open, sub.c.close)
        .where(sub.c.rn <= bars)
        .order_by(sub.c.code, sub.c.ts.asc())
    )
    out: dict[str, list[tuple[datetime, float, float]]] = {}
    for code, ts, open_, close in db.execute(stmt):
        out.setdefault(code, []).append((ts, open_, close))
    return out


_bench_cache: dict[str, tuple[str, dict[datetime, float]]] = {}


def _benchmark_closes(code: str) -> dict[datetime, float]:
    """基准指数收盘价（ts → close），按天缓存；数据源不支持时回退空。"""
    from app.providers.factory import get_provider

    today = datetime.now().strftime("%Y-%m-%d")
    cached = _bench_cache.get(code)
    if cached and cached[0] == today:
        return cached[1]
    try:
        candles = get_provider().get_index_kline(code, _MAX_BARS)
    except Exception:  # noqa: BLE001 — 基准拿不到就只回策略曲线
        return {}
    out = {c.ts: c.close for c in candles}
    _bench_cache[code] = (today, out)
    return out


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _empty_result(mode: str, hit_count: int = 0) -> dict[str, Any]:
    return {
        "metrics": {"mode": mode, "hitCount": hit_count, "eventCount": 0,
                    "annualizedReturn": 0.0, "maxDrawdown": 0.0, "sharpe": 0.0, "winRate": 0.0},
        "curve": [],
        "benchmark": [],
    }


def run(db: Session, dsl: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    dsl = validate_dsl(dsl)
    p = _clean_params(params or {}, dsl)
    all_rows = factor_rows(db)
    if dsl.get("technical"):
        # 事件模式：标量层作为股票池预筛（今日快照，已在 UI 披露）；
        # 纯技术策略只做 universe 过滤，不能走 execute——会触发"双空"校验
        pool = (
            execute({**dsl, "technical": []}, all_rows)
            if dsl["filters"]
            else apply_universe(dsl, all_rows)
        )
        return _run_event(db, dsl, p, pool)
    # 组合模式：传全市场行，内部按各调仓时点重选（今天不满足条件的
    # 股票，历史时点可能满足）
    return _run_portfolio(db, dsl, p, all_rows)


# ---- 事件驱动模式 ---------------------------------------------------------------


def _run_event(
    db: Session, dsl: dict[str, Any], p: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    tech = dsl["technical"]
    codes = [r["code"] for r in rows]
    need = technical.bars_needed(tech)
    depth = min(_MAX_BARS, p["period"] + need + p["hold"] * 3 + 2)
    rev = technical.reverse_signal(tech) if p["exit"] == "signal" else None

    trades: list[dict[str, Any]] = []
    paths: list[list[float]] = []
    for chunk in _chunks(codes, _CHUNK):
        for code, series in _bars_by_code(db, chunk, depth).items():
            closes = [c for _, _, c in series]
            n = len(series)
            if n < need + 2:
                continue
            sig = technical.signal_series(tech, closes)
            rev_sig = technical.signal_series(rev, closes) if rev else None
            i = max(need, n - 1 - p["period"])  # 只取回测窗口内的信号
            while i < n - 1:
                if not sig[i]:
                    i += 1
                    continue
                e_idx = i + 1
                entry_px = series[e_idx][1] if p["entry"] == "open" else series[e_idx][2]
                if entry_px <= 0:
                    i += 1
                    continue
                cap_idx = min(e_idx + p["hold"], n - 1)
                exit_idx = cap_idx
                if p["exit"] == "signal" and rev_sig is not None:
                    cap = min(e_idx + p["hold"] * 3, n - 1)  # 反向信号迟迟不来时的兜底
                    exit_idx = cap
                    for j in range(e_idx + 1, cap + 1):
                        if rev_sig[j]:
                            exit_idx = j
                            break
                elif p["exit"] == "stop":
                    for j in range(e_idx, cap_idx + 1):
                        r = series[j][2] / entry_px - 1
                        if r >= p["stop_gain"] / 100 or r <= -p["stop_loss"] / 100:
                            exit_idx = j
                            break
                exit_px = series[exit_idx][2]
                trades.append(
                    {
                        "ret": exit_px / entry_px - 1 - 2 * p["rate"],
                        "days": exit_idx - e_idx,
                        "entry_ts": series[e_idx][0],
                        "exit_ts": series[exit_idx][0],
                    }
                )
                # 平均收益路径（按持有期对齐；实际提前退出的用退出价截断）
                path = [
                    series[j][2] / entry_px - 1
                    for j in range(e_idx, min(e_idx + p["hold"], n - 1) + 1)
                ]
                paths.append(path)
                i = exit_idx + 1  # 单股同时只持一笔

    hit_count = len(codes)
    if not trades:
        return _empty_result("event", hit_count)

    rets = [t["ret"] for t in trades]
    bench = _benchmark_closes(p["benchmark"])
    excesses = []
    for t in trades:
        b0, b1 = bench.get(t["entry_ts"]), bench.get(t["exit_ts"])
        if b0 and b1:
            excesses.append(t["ret"] - (b1 / b0 - 1))

    # 曲线：所有事件的平均累计收益路径 D0..D{hold}（短路径用末值补齐）
    horizon = p["hold"] + 1
    curve = []
    for k in range(horizon):
        vals = [path[k] if k < len(path) else path[-1] for path in paths if path]
        if not vals:
            break
        curve.append({"t": f"D{k}", "v": round(1 + mean(vals), 4)})

    return {
        "metrics": {
            "mode": "event",
            "hitCount": hit_count,
            "eventCount": len(trades),
            "winRate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
            "avgReturn": round(mean(rets) * 100, 2),
            "medianReturn": round(median(rets) * 100, 2),
            "avgHoldDays": round(mean(t["days"] for t in trades), 1),
            "avgExcess": round(mean(excesses) * 100, 2) if excesses else None,
            "benchmarkName": _BENCHMARKS[p["benchmark"]],
        },
        "curve": curve,
        "benchmark": [],
    }


# ---- 组合模式 -------------------------------------------------------------------


def _period_key(ts: datetime, freq: str) -> tuple:
    if freq == "weekly":
        iso = ts.isocalendar()
        return (iso[0], iso[1])
    if freq == "quarterly":
        return (ts.year, (ts.month - 1) // 3)
    return (ts.year, ts.month)


def _trading_calendar(db: Session, bench: dict[datetime, float], period: int) -> list[datetime]:
    """回测交易日历：优先用基准指数的日期序列，基准不可用时退回参照股。"""
    if bench:
        cal = sorted(bench.keys())
    else:
        cal = [
            r[0]
            for r in db.execute(
                select(Kline.ts)
                .where(Kline.code == "600519", Kline.period == "1d")
                .order_by(Kline.ts.asc())
            )
        ]
    return cal[-(period + 1) :]


def _bar_lookup(
    db: Session, dates: list[datetime]
) -> dict[datetime, dict[str, tuple[float, float]]]:
    """指定日期集的全市场 (close, volume)，按日期分组。"""
    out: dict[datetime, dict[str, tuple[float, float]]] = {}
    if not dates:
        return out
    for code, ts, close, vol in db.execute(
        select(Kline.code, Kline.ts, Kline.close, Kline.volume).where(
            Kline.period == "1d", Kline.ts.in_(dates)
        )
    ):
        out.setdefault(ts, {})[code] = (close, vol)
    return out


def _snapshot_rows(db: Session, ts: datetime) -> list[dict[str, Any]]:
    """factor_history 某日快照 → DSL 因子行（name/market 用当前值，ST 状态近似）。"""
    info = {s.code: s for s in db.execute(select(StockInfo)).scalars().all()}
    rows = []
    for s in db.execute(select(FactorSnapshot).where(FactorSnapshot.ts == ts)).scalars():
        meta = info.get(s.code)
        rows.append(
            {
                "code": s.code,
                "name": meta.name if meta else s.code,
                "market": meta.market if meta else "SH",
                "industry": s.industry,
                "pe": s.pe, "pb": s.pb, "roe": s.roe,
                "turnover_rate": s.turnover_rate, "turnover": s.turnover,
                "market_cap": s.market_cap, "change_pct": s.change_pct,
                "price": s.price, "dividend_yield": s.dividend_yield,
            }
        )
    return rows


def _reconstructed_rows(
    rows_today: list[dict[str, Any]],
    ref_bars: dict[str, tuple[float, float]],
    day_bars: dict[str, tuple[float, float]],
    prev_bars: dict[str, tuple[float, float]],
) -> list[dict[str, Any]]:
    """无快照日期的近似因子行：价格相关因子按当日收盘缩放（股本/盈利视为不变），
    成交额按当日量价直算；ROE/股息率/行业取当前值（季度级慢变）。"""
    out = []
    for r in rows_today:
        code = r["code"]
        ref = ref_bars.get(code)
        day = day_bars.get(code)
        if not ref or not day or ref[0] <= 0:
            continue  # 当时未上市 / 无数据
        k = day[0] / ref[0]
        prev = prev_bars.get(code)
        out.append(
            {
                **r,
                "price": day[0],
                "market_cap": round(r["market_cap"] * k, 2),
                "pe": round(r["pe"] * k, 2) if r["pe"] else None,
                "pb": round(r["pb"] * k, 3),
                "change_pct": round((day[0] / prev[0] - 1) * 100, 2) if prev and prev[0] else 0.0,
                "turnover": round(day[1] * 100 * day[0] / 1e8, 2),
                "turnover_rate": (
                    round(r["turnover_rate"] * day[1] / ref[1], 2) if ref[1] else r["turnover_rate"]
                ),
            }
        )
    return out


def _run_portfolio(
    db: Session, dsl: dict[str, Any], p: dict[str, Any], rows_today: list[dict[str, Any]]
) -> dict[str, Any]:
    scalar_dsl = {**dsl, "technical": []}
    hit_count = len(execute(scalar_dsl, rows_today)) if dsl["filters"] else len(rows_today)

    bench = _benchmark_closes(p["benchmark"])
    calendar = _trading_calendar(db, bench, p["period"])
    if len(calendar) < 2:
        return _empty_result("portfolio", hit_count)

    # 调仓期边界：日历上周期键变化的位置；每期成分在上一交易日收盘后选定
    boundaries = [0] + [
        i
        for i in range(1, len(calendar))
        if _period_key(calendar[i], p["rebalance"]) != _period_key(calendar[i - 1], p["rebalance"])
    ]
    sel_dates: list[datetime] = []
    all_cal = sorted(bench.keys()) if bench else calendar
    for b in boundaries:
        d = calendar[b]
        pos = all_cal.index(d)
        sel_dates.append(all_cal[pos - 1] if pos > 0 else d)

    snap_days = {
        r[0]
        for r in db.execute(
            select(FactorSnapshot.ts).where(FactorSnapshot.ts.in_(sel_dates)).distinct()
        )
    }
    # 重构需要：各选股日及其前一日的收盘/量 + 今日参照价
    prev_of = {d: all_cal[all_cal.index(d) - 1] for d in sel_dates if all_cal.index(d) > 0}
    ref_date = calendar[-1]
    lookup_dates = sorted({*sel_dates, *prev_of.values(), ref_date} - snap_days | {ref_date})
    bar_lookup = _bar_lookup(db, lookup_dates)
    ref_bars = bar_lookup.get(ref_date, {})

    # 每个调仓期重选成分并定权重
    period_sel: list[dict[str, float]] = []  # 每期 code → weight
    pit_periods = 0
    for d in sel_dates:
        if d in snap_days:
            rows_d = _snapshot_rows(db, d)
            pit_periods += 1
        else:
            rows_d = _reconstructed_rows(
                rows_today, ref_bars, bar_lookup.get(d, {}), bar_lookup.get(prev_of.get(d), {})
            )
        picked = execute(scalar_dsl, rows_d) if dsl["filters"] else apply_universe(dsl, rows_d)
        if p["max_pos"]:
            picked = sorted(picked, key=lambda r: r.get("market_cap") or 0, reverse=True)[
                : p["max_pos"]
            ]
        period_sel.append(
            {
                r["code"]: (r.get("market_cap") or 0.0) if p["weighting"] == "cap" else 1.0
                for r in picked
                if p["weighting"] == "equal" or (r.get("market_cap") or 0) > 0
            }
        )

    union_codes = sorted({c for sel in period_sel for c in sel})
    if not union_codes:
        return _empty_result("portfolio", hit_count)
    close_map: dict[str, dict[datetime, float]] = {}
    for chunk in _chunks(union_codes, _CHUNK):
        for code, s in _bars_by_code(db, chunk, p["period"] + 1).items():
            close_map[code] = {ts: close for ts, _, close in s}

    # 日收益：用当日所属调仓期的成分与权重
    boundary_set = set(boundaries)
    daily_returns: list[float] = []
    dates: list[datetime] = []
    active = 0
    for i in range(1, len(calendar)):
        if i in boundary_set:
            active = boundaries.index(i)
        weights = period_sel[active]
        d0, d1 = calendar[i - 1], calendar[i]
        num = den = 0.0
        for c, w in weights.items():
            m = close_map.get(c)
            if not m:
                continue
            p0, p1 = m.get(d0), m.get(d1)
            if p0 and p1 and p0 > 0:
                w = w or 1.0
                num += w * (p1 / p0 - 1)
                den += w
        if not den:
            continue
        r = num / den
        if i in boundary_set:
            r -= 2 * p["rate"]  # 调仓成本
        daily_returns.append(r)
        dates.append(d1)

    equity, peak, max_dd = 1.0, 1.0, 0.0
    curve: list[dict[str, Any]] = [{"t": calendar[0].strftime("%Y-%m-%d"), "v": 1.0}]
    for r, d in zip(daily_returns, dates):
        equity *= 1 + r
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1)
        curve.append({"t": d.strftime("%Y-%m-%d"), "v": round(equity, 4)})

    n = len(daily_returns)
    annualized = (equity ** (TRADING_DAYS / n) - 1) if n else 0.0
    vol = pstdev(daily_returns) if n > 1 else 0.0
    sharpe = (mean(daily_returns) / vol * (TRADING_DAYS**0.5)) if vol else 0.0
    win_rate = (sum(1 for r in daily_returns if r > 0) / n) if n else 0.0

    # 基准曲线对齐同一交易日历，起点归一
    bench = _benchmark_closes(p["benchmark"])
    bench_curve: list[dict[str, Any]] = []
    base = bench.get(calendar[0])
    if base:
        for d in calendar:
            v = bench.get(d)
            if v:
                bench_curve.append({"t": d.strftime("%Y-%m-%d"), "v": round(v / base, 4)})
    bench_total = (bench_curve[-1]["v"] - 1) if bench_curve else None

    if len(curve) > 120:
        step = len(curve) // 120
        curve = curve[::step] + [curve[-1]]
    if len(bench_curve) > 120:
        step = len(bench_curve) // 120
        bench_curve = bench_curve[::step] + [bench_curve[-1]]

    return {
        "metrics": {
            "mode": "portfolio",
            "hitCount": hit_count,
            "annualizedReturn": round(annualized * 100, 2),
            "maxDrawdown": round(max_dd * 100, 2),
            "sharpe": round(sharpe, 2),
            "winRate": round(win_rate * 100, 1),
            "totalReturn": round((equity - 1) * 100, 2),
            "benchmarkReturn": round(bench_total * 100, 2) if bench_total is not None else None,
            "excessReturn": (
                round((equity - 1 - bench_total) * 100, 2) if bench_total is not None else None
            ),
            "benchmarkName": _BENCHMARKS[p["benchmark"]],
            "rebalances": len(period_sel),
            "pitPeriods": pit_periods,  # 用真实快照重选的期数（随归档积累增长）
        },
        "curve": curve,
        "benchmark": bench_curve,
    }


def write_curve(backtest_id: int, payload: dict[str, Any]) -> str:
    os.makedirs(settings.reports_dir, exist_ok=True)
    path = os.path.join(settings.reports_dir, f"backtest-{backtest_id}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return path
