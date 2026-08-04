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
from app.services.dsl import CROSS_OPS, apply_universe, execute, match_filter, validate_dsl
from app.services.market import factor_rows

TRADING_DAYS = 252
_MAX_BARS = 1300  # ~5 年：日 K 深度回补（scripts/backfill_kline.py）后的窗口上限
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
        "period": int(num("periodDays", 250, 60, 1250)),
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
        # 事件模式账户口径：最大同时持仓数，每笔占 1/max_concurrent 仓位
        "max_concurrent": int(num("maxConcurrent", 10, 1, 50)),
    }


def _bars_by_code(
    db: Session, codes: list[str], bars: int
) -> dict[str, list[tuple]]:
    """每只股票最近 `bars` 根 (ts, open, close, high, low, volume)，升序。"""
    if not codes:
        return {}
    rn = func.row_number().over(partition_by=Kline.code, order_by=Kline.ts.desc()).label("rn")
    sub = (
        select(
            Kline.code, Kline.ts, Kline.open, Kline.close, Kline.high, Kline.low,
            Kline.volume, rn,
        )
        .where(Kline.period == "1d", Kline.code.in_(codes))
        .subquery()
    )
    stmt = (
        select(
            sub.c.code, sub.c.ts, sub.c.open, sub.c.close, sub.c.high, sub.c.low, sub.c.volume
        )
        .where(sub.c.rn <= bars)
        .order_by(sub.c.code, sub.c.ts.asc())
    )
    out: dict[str, list[tuple]] = {}
    for code, ts, open_, close, high, low, volume in db.execute(stmt):
        out.setdefault(code, []).append((ts, open_, close, high, low, volume or 0))
    return out


# ---- 可交易性（一字板）-----------------------------------------------------------


def _limit_pct(code: str, name: str) -> float:
    """涨跌停幅度：主板 10%，创业板/科创板 20%，ST 5%。"""
    if "ST" in name:
        return 0.05
    if code.startswith(("30", "68")):
        return 0.20
    return 0.10


def _one_word(
    series: list[tuple], j: int, pct: float, direction: int
) -> bool:
    """第 j 根是否一字板（无法成交）：高低价相等且收于涨/跌停价附近。

    direction=+1 判涨停（买不进），-1 判跌停（卖不出）。0.998 容差吸收
    交易所按分报价的取整误差。
    """
    if j <= 0:
        return False
    close, high, low = series[j][2], series[j][3], series[j][4]
    prev_close = series[j - 1][2]
    if prev_close <= 0 or abs(high - low) > 1e-9:
        return False
    # 收盘须贴着涨/跌停价（±0.4% 容差吸收 0.01 元取整）——只超不贴不算：
    # 一字板价格恰为 prev×(1±pct)，涨幅大于该值的平 bar 是新股/数据噪音
    limit_px = prev_close * (1 + pct * direction)
    return abs(close - limit_px) <= limit_px * 0.004


def _rebalance_cost(
    prev_weights: dict[str, float], new_weights: dict[str, float], rate: float
) -> float:
    """按实际换手计成本：卖出份额 × rate + 买入份额 × rate（权重先归一）。

    成分不变 → 0；全换手 → 2×rate；首期建仓（prev 为空）→ 买入单边 rate。
    """

    def norm(w: dict[str, float]) -> dict[str, float]:
        total = sum(w.values())
        return {k: v / total for k, v in w.items()} if total > 0 else {}

    a, b = norm(prev_weights), norm(new_weights)
    if not b:
        return 0.0
    sold = sum(max(a.get(k, 0.0) - b.get(k, 0.0), 0.0) for k in a)
    bought = sum(max(b.get(k, 0.0) - a.get(k, 0.0), 0.0) for k in b)
    return (sold + bought) * rate


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
        "avgPath": [],
        "trades": [],
        "rebalanceRecords": [],
    }


# 可从 K 线逐日复算的日级价格因子：事件模式按信号日检查，
# 而不是拿"回测运行当天"的快照值冒充历史（如"剔除涨幅>3%"须看信号日的涨幅）
_DAY_FACTORS = {
    "change_pct", "high_change_pct", "low_change_pct", "open_change_pct", "intraday_change_pct",
}


def _split_day_filters(
    dsl: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """filters → (信号日逐日检查的日级条件, 留在池预筛的其余条件)。

    截面算子/行业中位数引用无法按单股单日复算，仍留在预筛。
    """
    day: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    for f in dsl["filters"]:
        eligible = f["factor"] in _DAY_FACTORS and "ref" not in f and f["op"] not in CROSS_OPS
        (day if eligible else rest).append(f)
    return day, rest


def run(db: Session, dsl: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    dsl = validate_dsl(dsl)
    p = _clean_params(params or {}, dsl)
    all_rows = factor_rows(db)
    if dsl.get("technical"):
        # 事件模式：日级价格条件按信号日逐日检查；其余标量/打分层作为
        # 股票池预筛（今日快照，已在 UI 披露）。
        # 无预筛条件时只做 universe 过滤，不能走 execute——会触发"全空"校验
        day_filters, pool_filters = _split_day_filters(dsl)
        pool = (
            execute({**dsl, "filters": pool_filters, "technical": []}, all_rows)
            if pool_filters or dsl.get("score")
            else apply_universe(dsl, all_rows)
        )
        return _run_event(db, dsl, p, pool, day_filters)
    # 组合模式：传全市场行，内部按各调仓时点重选（今天不满足条件的
    # 股票，历史时点可能满足）
    return _run_portfolio(db, dsl, p, all_rows)


# ---- 事件驱动模式 ---------------------------------------------------------------


def _stop_exit(
    series: list[tuple],
    e_idx: int,
    cap_idx: int,
    entry_px: float,
    stop_gain: float,
    stop_loss: float,
    entry_mode: str,
) -> tuple[int, float, str] | None:
    """盘中止盈止损：按当日 high/low 触发、按触发价成交。

    - 跳空越过阈值：按开盘价成交（止损更差、止盈更好，符合真实委托）
    - 同日高低价双触发：日线无法排序盘中先后，保守计为止损
    - entry="close" 时入场日盘中已过，从次日起判；entry="open" 入场日全天有效
    - 到 cap_idx 未触发返回 None（按持有到期处理）
    """
    gain_px = entry_px * (1 + stop_gain / 100)
    loss_px = entry_px * (1 - stop_loss / 100)
    start = e_idx if entry_mode == "open" else e_idx + 1
    for j in range(start, cap_idx + 1):
        _ts, o, _c, high, low, _v = series[j]
        open_px = entry_px if j == e_idx else o  # 入场日从成交价起算
        if open_px <= loss_px:
            return j, open_px, "stop_loss"
        if open_px >= gain_px:
            return j, open_px, "stop_gain"
        if low <= loss_px:
            return j, loss_px, "stop_loss"
        if high >= gain_px:
            return j, gain_px, "stop_gain"
    return None


def _day_row(series: list[tuple], i: int) -> dict[str, float | None] | None:
    """第 i 根 K 线的日级价格因子（相对前收的涨跌幅）；无前收时 None。"""
    prev_close = series[i - 1][2] if i > 0 else 0.0
    if not prev_close:
        return None
    _ts, o, c, high, low, _v = series[i]
    return {
        "change_pct": (c / prev_close - 1) * 100,
        "high_change_pct": (high / prev_close - 1) * 100 if high else None,
        "low_change_pct": (low / prev_close - 1) * 100 if low else None,
        "open_change_pct": (o / prev_close - 1) * 100 if o else None,
        "intraday_change_pct": (c / o - 1) * 100 if o else None,
    }


def _candidates_for_stock(
    code: str,
    name: str,
    series: list[tuple],
    p: dict[str, Any],
    tech: list[dict[str, Any]],
    rev: list[dict[str, Any]] | None,
    need: int,
    day_filters: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """一只股票的候选交易（含一字板顺延/放弃）。返回 (candidates, 因涨停放弃的信号数)。"""
    bars = technical.Bars(
        close=[bar[2] for bar in series],
        volume=[bar[5] for bar in series],
        open=[bar[1] for bar in series],
        high=[bar[3] for bar in series],
        low=[bar[4] for bar in series],
    )
    n = len(series)
    if n < need + 2:
        return [], 0
    pct = _limit_pct(code, name)
    sig = technical.signal_series(tech, bars)
    rev_sig = technical.signal_series(rev, bars) if rev else None

    cands: list[dict[str, Any]] = []
    skipped_limit = 0
    i = max(need, n - 1 - p["period"])  # 只取回测窗口内的信号
    while i < n - 1:
        if not sig[i]:
            i += 1
            continue
        # 日级价格条件按信号日的 K 线判定（与实盘"盘后选股看当天值"同口径）
        if day_filters:
            row = _day_row(series, i)
            if row is None or not all(match_filter(f, row) for f in day_filters):
                i += 1
                continue
        # 入场：信号次日，一字涨停顺延，连续 3 日买不进则放弃该信号
        e_idx = None
        for e in range(i + 1, min(i + 4, n)):
            if not _one_word(series, e, pct, +1):
                e_idx = e
                break
        if e_idx is None:
            skipped_limit += 1
            i += 1
            continue
        entry_px = series[e_idx][1] if p["entry"] == "open" else series[e_idx][2]
        if entry_px <= 0:
            i += 1
            continue

        cap_idx = min(e_idx + p["hold"], n - 1)
        exit_idx, reason = cap_idx, "hold"
        stop_px: float | None = None
        if p["exit"] == "signal" and rev_sig is not None:
            cap = min(e_idx + p["hold"] * 3, n - 1)  # 反向信号迟迟不来时的兜底
            exit_idx = cap
            for j in range(e_idx + 1, cap + 1):
                if rev_sig[j]:
                    exit_idx, reason = j, "signal"
                    break
        elif p["exit"] == "stop":
            hit = _stop_exit(
                series, e_idx, cap_idx, entry_px, p["stop_gain"], p["stop_loss"], p["entry"]
            )
            if hit:
                exit_idx, stop_px, reason = hit[0], hit[1], hit[2]
        # 出场：一字跌停卖不出则顺延，最多 5 日后按当日收盘强平；
        # 被顺延的止盈/止损单拿不到触发价，按实际卖出日收盘计
        moves = 0
        while exit_idx < n - 1 and moves < 5 and _one_word(series, exit_idx, pct, -1):
            exit_idx += 1
            moves += 1

        exit_px = stop_px if (stop_px is not None and moves == 0) else series[exit_idx][2]
        # 逐日收益：入场日为成交价→收盘（扣买入单边），此后 close-to-close，
        # 出场日按实际出场价并扣卖出单边；入场当日即触发止盈止损则单日结清
        daily: dict[datetime, float] = {}
        if exit_idx == e_idx:
            daily[series[e_idx][0]] = exit_px / entry_px - 1 - 2 * p["rate"]
        else:
            daily[series[e_idx][0]] = series[e_idx][2] / entry_px - 1 - p["rate"]
            for j in range(e_idx + 1, exit_idx + 1):
                px = exit_px if j == exit_idx else series[j][2]
                rj = px / series[j - 1][2] - 1
                if j == exit_idx:
                    rj -= p["rate"]
                daily[series[j][0]] = rj
        cands.append(
            {
                "code": code,
                "name": name,
                "ret": exit_px / entry_px - 1 - 2 * p["rate"],
                "days": exit_idx - e_idx,
                "entry_ts": series[e_idx][0],
                "exit_ts": series[exit_idx][0],
                "entry_px": round(entry_px, 3),
                "exit_px": round(exit_px, 3),
                "reason": reason,
                "daily": daily,
                # 平均收益路径（按持有期对齐；提前退出的用退出价截断）
                "path": [
                    series[j][2] / entry_px - 1
                    for j in range(e_idx, min(e_idx + p["hold"], n - 1) + 1)
                ],
            }
        )
        i = exit_idx + 1  # 单股同时只持一笔
    return cands, skipped_limit


def _run_event(
    db: Session,
    dsl: dict[str, Any],
    p: dict[str, Any],
    rows: list[dict[str, Any]],
    day_filters: list[dict[str, Any]],
) -> dict[str, Any]:
    tech = dsl["technical"]
    codes = [r["code"] for r in rows]
    names = {r["code"]: r["name"] for r in rows}
    need = technical.bars_needed(tech)
    depth = min(_MAX_BARS, p["period"] + need + p["hold"] * 3 + 2)
    rev = technical.reverse_signal(tech) if p["exit"] == "signal" else None
    max_c = p["max_concurrent"]

    candidates: list[dict[str, Any]] = []
    skipped_limit = 0
    for chunk in _chunks(codes, _CHUNK):
        for code, series in _bars_by_code(db, chunk, depth).items():
            cands, skipped = _candidates_for_stock(
                code, names.get(code, code), series, p, tech, rev, need, day_filters
            )
            candidates.extend(cands)
            skipped_limit += skipped

    hit_count = len(codes)
    bench = _benchmark_closes(p["benchmark"])
    calendar = _trading_calendar(db, bench, p["period"])

    # 账户口径模拟：每笔占 1/max_concurrent 仓位，同日信号多于空位按代码序取前 N；
    # 当日退出的仓位收盘才腾出，次日方可复用
    by_entry: dict[datetime, list[dict[str, Any]]] = {}
    for c in sorted(candidates, key=lambda c: (c["entry_ts"], c["code"])):
        by_entry.setdefault(c["entry_ts"], []).append(c)
    active: dict[str, dict[str, Any]] = {}
    executed: list[dict[str, Any]] = []
    skipped_capacity = 0
    equity = 1.0
    daily_returns: list[float] = []
    curve: list[dict[str, Any]] = []
    bench_curve: list[dict[str, Any]] = []
    base = bench.get(calendar[0]) if calendar else None
    for d in calendar:
        for cand in by_entry.get(d, ()):  # 入场（占用空位）
            if len(active) >= max_c:
                skipped_capacity += 1
                continue
            active[cand["code"]] = cand
            executed.append(cand)
        r_day = (
            sum(cand["daily"].get(d, 0.0) for cand in active.values()) / max_c
            if active
            else 0.0
        )
        equity *= 1 + r_day
        daily_returns.append(r_day)
        curve.append({"t": d.strftime("%Y-%m-%d"), "v": round(equity, 4)})
        if base:
            v = bench.get(d)
            if v:
                bench_curve.append({"t": d.strftime("%Y-%m-%d"), "v": round(v / base, 4)})
        for code in [c for c, cd in active.items() if cd["exit_ts"] == d]:
            del active[code]

    if not executed:
        empty = _empty_result("event", hit_count)
        empty["metrics"]["skippedByLimit"] = skipped_limit
        empty["metrics"]["skippedByCapacity"] = skipped_capacity
        return empty

    rets = [t["ret"] for t in executed]
    excesses = []
    for t in executed:
        b0, b1 = bench.get(t["entry_ts"]), bench.get(t["exit_ts"])
        if b0 and b1:
            excesses.append(t["ret"] - (b1 / b0 - 1))

    total_return = equity - 1
    peak, max_dd = 1.0, 0.0
    eq = 1.0
    for r in daily_returns:
        eq *= 1 + r
        peak = max(peak, eq)
        max_dd = min(max_dd, eq / peak - 1)
    n_days = len(daily_returns)
    annualized = (equity ** (TRADING_DAYS / n_days) - 1) if n_days else 0.0
    vol = pstdev(daily_returns) if n_days > 1 else 0.0
    sharpe = (mean(daily_returns) / vol * (TRADING_DAYS**0.5)) if vol else 0.0

    bench_total = (bench_curve[-1]["v"] - 1) if bench_curve else None
    for series_ in (curve, bench_curve):
        if len(series_) > 120:
            step = len(series_) // 120
            series_[:] = series_[::step] + [series_[-1]]

    # 次曲线（对齐入场日）：已执行事件的平均累计收益路径 D0..D{hold}
    horizon = p["hold"] + 1
    avg_path = []
    for k in range(horizon):
        vals = [t["path"][k] if k < len(t["path"]) else t["path"][-1] for t in executed if t["path"]]
        if not vals:
            break
        avg_path.append({"t": f"D{k}", "v": round(1 + mean(vals), 4)})

    # 逐笔明细：全量落盘（分页接口按需读取），超上限截断并在指标注明
    _TRADES_CAP = 50_000
    trade_records = [
        {
            "code": t["code"],
            "name": t["name"],
            "entryDate": t["entry_ts"].strftime("%Y-%m-%d"),
            "entryPx": t["entry_px"],
            "exitDate": t["exit_ts"].strftime("%Y-%m-%d"),
            "exitPx": t["exit_px"],
            "ret": round(t["ret"] * 100, 2),
            "days": t["days"],
            "reason": t["reason"],
        }
        for t in executed[:_TRADES_CAP]
    ]

    return {
        "metrics": {
            "mode": "event",
            "hitCount": hit_count,
            "eventCount": len(executed),
            "winRate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
            "avgReturn": round(mean(rets) * 100, 2),
            "medianReturn": round(median(rets) * 100, 2),
            "avgHoldDays": round(mean(t["days"] for t in executed), 1),
            "avgExcess": round(mean(excesses) * 100, 2) if excesses else None,
            "benchmarkName": _BENCHMARKS[p["benchmark"]],
            "totalReturn": round(total_return * 100, 2),
            "annualizedReturn": round(annualized * 100, 2),
            "maxDrawdown": round(max_dd * 100, 2),
            "sharpe": round(sharpe, 2),
            "benchmarkReturn": round(bench_total * 100, 2) if bench_total is not None else None,
            "maxConcurrent": max_c,
            "skippedByLimit": skipped_limit,        # 一字涨停买不进而放弃的信号数
            "skippedByCapacity": skipped_capacity,  # 仓位满而放弃的信号数
            "tradesTruncated": len(executed) > _TRADES_CAP,
            # AI 诊断用的归因摘要：亏得最狠的 5 笔
            "worstTrades": [
                {
                    "code": t["code"],
                    "name": t["name"],
                    "ret": round(t["ret"] * 100, 2),
                    "entryDate": t["entry_ts"].strftime("%Y-%m-%d"),
                    "reason": t["reason"],
                }
                for t in sorted(executed, key=lambda t: t["ret"])[:5]
            ],
        },
        "curve": curve,
        "benchmark": bench_curve,
        "avgPath": avg_path,
        "trades": trade_records,
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


_Bar = tuple[float, float, float, float, float]  # (close, volume, high, low, open)


def _bar_lookup(db: Session, dates: list[datetime]) -> dict[datetime, dict[str, _Bar]]:
    """指定日期集的全市场日 K 关键字段，按日期分组。"""
    out: dict[datetime, dict[str, _Bar]] = {}
    if not dates:
        return out
    for code, ts, close, vol, high, low, open_ in db.execute(
        select(
            Kline.code, Kline.ts, Kline.close, Kline.volume, Kline.high, Kline.low, Kline.open
        ).where(Kline.period == "1d", Kline.ts.in_(dates))
    ):
        out.setdefault(ts, {})[code] = (close, vol, high, low, open_)
    return out


def _hlo_change_pct(
    code: str, day_bars: dict[str, _Bar], prev_bars: dict[str, _Bar]
) -> tuple[float | None, float | None, float | None, float | None]:
    """当日最高/最低/开盘价相对前收 + 收盘相对今开（日内）的涨跌幅；
    缺 K 线时为 None（不通过数值筛）。"""
    day = day_bars.get(code)
    prev = prev_bars.get(code)
    if not day or not prev or not prev[0]:
        return None, None, None, None
    return (
        round((day[2] / prev[0] - 1) * 100, 2) if day[2] else None,
        round((day[3] / prev[0] - 1) * 100, 2) if day[3] else None,
        round((day[4] / prev[0] - 1) * 100, 2) if day[4] else None,
        round((day[0] / day[4] - 1) * 100, 2) if day[4] and day[0] else None,
    )


def _snapshot_rows(
    db: Session,
    ts: datetime,
    day_bars: dict[str, _Bar],
    prev_bars: dict[str, _Bar],
) -> list[dict[str, Any]]:
    """factor_history 某日快照 → DSL 因子行（name/market 用当前值，ST 状态近似）。

    快照表不存 high/low/open，相应涨跌幅从当日/前日 K 线补算。
    """
    info = {s.code: s for s in db.execute(select(StockInfo)).scalars().all()}
    rows = []
    for s in db.execute(select(FactorSnapshot).where(FactorSnapshot.ts == ts)).scalars():
        meta = info.get(s.code)
        hi, lo, opn, intra = _hlo_change_pct(s.code, day_bars, prev_bars)
        rows.append(
            {
                "code": s.code,
                "name": meta.name if meta else s.code,
                "market": meta.market if meta else "SH",
                "industry": s.industry,
                "pe": s.pe, "pb": s.pb, "roe": s.roe,
                "turnover_rate": s.turnover_rate, "turnover": s.turnover,
                "market_cap": s.market_cap, "change_pct": s.change_pct,
                "high_change_pct": hi, "low_change_pct": lo, "open_change_pct": opn,
                "intraday_change_pct": intra,
                "price": s.price, "dividend_yield": s.dividend_yield,
            }
        )
    return rows


def _reconstructed_rows(
    rows_today: list[dict[str, Any]],
    ref_bars: dict[str, _Bar],
    day_bars: dict[str, _Bar],
    prev_bars: dict[str, _Bar],
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
        hi, lo, opn, intra = _hlo_change_pct(code, day_bars, prev_bars)
        out.append(
            {
                **r,
                "price": day[0],
                "market_cap": round(r["market_cap"] * k, 2),
                "pe": round(r["pe"] * k, 2) if r["pe"] else None,
                "pb": round(r["pb"] * k, 3),
                "change_pct": round((day[0] / prev[0] - 1) * 100, 2) if prev and prev[0] else 0.0,
                "high_change_pct": hi, "low_change_pct": lo, "open_change_pct": opn,
                "intraday_change_pct": intra,
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
    has_scalar = bool(dsl["filters"] or dsl.get("score"))
    hit_count = len(execute(scalar_dsl, rows_today)) if has_scalar else len(rows_today)

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
    # 各选股日及其前一日的 K 线（重构因子 + 最高价涨跌幅）+ 今日参照价
    prev_of = {d: all_cal[all_cal.index(d) - 1] for d in sel_dates if all_cal.index(d) > 0}
    ref_date = calendar[-1]
    lookup_dates = sorted({*sel_dates, *prev_of.values(), ref_date})
    bar_lookup = _bar_lookup(db, lookup_dates)
    ref_bars = bar_lookup.get(ref_date, {})

    # 每个调仓期重选成分并定权重
    period_sel: list[dict[str, float]] = []  # 每期 code → weight
    period_names: list[dict[str, str]] = []
    pit_periods = 0
    for d in sel_dates:
        if d in snap_days:
            rows_d = _snapshot_rows(
                db, d, bar_lookup.get(d, {}), bar_lookup.get(prev_of.get(d), {})
            )
            pit_periods += 1
        else:
            rows_d = _reconstructed_rows(
                rows_today, ref_bars, bar_lookup.get(d, {}), bar_lookup.get(prev_of.get(d), {})
            )
        picked = execute(scalar_dsl, rows_d) if has_scalar else apply_universe(dsl, rows_d)
        if p["max_pos"]:
            if dsl.get("score"):
                picked = picked[: p["max_pos"]]  # execute 已按得分降序
            else:
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
        period_names.append({r["code"]: r["name"] for r in picked})

    union_codes = sorted({c for sel in period_sel for c in sel})
    if not union_codes:
        return _empty_result("portfolio", hit_count)
    close_map: dict[str, dict[datetime, float]] = {}
    for chunk in _chunks(union_codes, _CHUNK):
        for code, s in _bars_by_code(db, chunk, p["period"] + 1).items():
            close_map[code] = {ts: close for ts, _o, close, *_rest in s}

    # 调仓成本按实际换手：每期与上期权重向量的差额 × 费率（首期是建仓买入单边）
    period_cost = [
        _rebalance_cost(period_sel[k - 1] if k else {}, period_sel[k], p["rate"])
        for k in range(len(period_sel))
    ]

    # 日收益：用当日所属调仓期的成分与权重；顺带累计每期收益
    boundary_set = set(boundaries)
    daily_returns: list[float] = []
    dates: list[datetime] = []
    period_equity = [1.0] * len(period_sel)
    active = 0
    first_day = True
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
            r -= period_cost[active]  # 换手成本
        elif first_day:
            r -= period_cost[0]  # 首期建仓成本记在第一个计算日
        first_day = False
        period_equity[active] *= 1 + r
        daily_returns.append(r)
        dates.append(d1)

    # 调仓记录：每期新进/调出（名单各截前 100 只，计数保留全量）
    rebalance_records: list[dict[str, Any]] = []
    for k, b in enumerate(boundaries):
        cur, prev = set(period_sel[k]), set(period_sel[k - 1]) if k else set()
        added = sorted(cur - prev)
        removed = sorted(prev - cur)
        names_k = period_names[k]
        names_prev = period_names[k - 1] if k else {}
        rebalance_records.append(
            {
                "date": calendar[b].strftime("%Y-%m-%d"),
                "holdings": len(cur),
                "addedCount": len(added) if k else 0,  # 首期是建仓，不算"新进"
                "removedCount": len(removed),
                "added": [] if not k else [
                    {"code": c, "name": names_k.get(c, c)} for c in added[:100]
                ],
                "removed": [{"code": c, "name": names_prev.get(c, c)} for c in removed[:100]],
                "periodReturn": round((period_equity[k] - 1) * 100, 2),
            }
        )

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
            # AI 诊断用的归因摘要：亏得最狠的 3 个调仓期
            "worstPeriods": [
                {"date": rec["date"], "ret": rec["periodReturn"], "holdings": rec["holdings"]}
                for rec in sorted(rebalance_records, key=lambda r: r["periodReturn"])[:3]
            ],
        },
        "curve": curve,
        "benchmark": bench_curve,
        "rebalanceRecords": rebalance_records,
    }


def write_curve(backtest_id: int, payload: dict[str, Any]) -> str:
    os.makedirs(settings.reports_dir, exist_ok=True)
    path = os.path.join(settings.reports_dir, f"backtest-{backtest_id}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return path
