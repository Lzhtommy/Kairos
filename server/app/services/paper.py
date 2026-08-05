"""策略自动模拟盘：与事件回测同一套成交语义，逐交易日前瞻结算。

口径（与 backtest 事件模式逐条对齐，两条曲线才可比）：
- 信号 = 盘后 StrategyRun 的命中；次日开盘入场，每笔占 1/maxConcurrent 仓位；
- 入场日一字涨停顺延，连续 3 个交易日买不进放弃（计入 skippedByLimit）；
- 仓位满时按代码序放弃多余信号（计入 skippedByCapacity）；
  当日退出的仓位收盘才腾出，次日方可复用；
- 退出：hold 持有到期收盘卖 / stop 盘中止盈止损（跳空按开盘、双触发保守计止损）/
  signal 反向信号日收盘卖（hold*3 兜底）；一字跌停卖不出顺延，最多 5 日后强平；
- 净值：日收益 = Σ持仓日收益 / maxConcurrent，空仓部分现金持平；
  买卖各扣单边费率。

停机容忍：按交易日日历逐日补结算（用历史 K 线），不会跳日漏止损。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.market import Kline
from app.models.strategy import (
    PaperAccount,
    PaperEquity,
    PaperPosition,
    PaperTrade,
    Strategy,
    StrategyRun,
)
from app.services import technical
from app.services.backtest import _limit_pct

logger = logging.getLogger("kairos.paper")

_ENTRY_DEFER_MAX = 3   # 一字涨停顺延上限（交易日）
_EXIT_DEFER_MAX = 5    # 一字跌停顺延上限
_PENDING_CAP_X = 3     # 每日新信号排队上限 = maxConcurrent × 3（按代码序，与回测取前 N 一致）
_CAL_REF = "600519"    # 交易日历参照股（与 backtest._trading_calendar 兜底一致）

Bar = tuple[float, float, float, float]  # (open, high, low, close)


def default_params() -> dict[str, Any]:
    return {"holdDays": 10, "exitRule": "hold", "stopGain": 15.0, "stopLoss": 8.0,
            "maxConcurrent": 10, "costRate": 0.0005}


def clean_params(raw: dict[str, Any] | None) -> dict[str, Any]:
    """夹紧到与回测 _clean_params 相同的安全范围。"""
    raw = raw or {}
    out = default_params()

    def num(key: str, lo: float, hi: float) -> None:
        try:
            out[key] = min(max(float(raw.get(key, out[key])), lo), hi)
        except (TypeError, ValueError):
            pass

    num("holdDays", 1, 60)
    num("stopGain", 1.0, 100.0)
    num("stopLoss", 1.0, 50.0)
    num("maxConcurrent", 1, 50)
    num("costRate", 0.0, 0.003)
    out["holdDays"] = int(out["holdDays"])
    out["maxConcurrent"] = int(out["maxConcurrent"])
    if raw.get("exitRule") in ("hold", "signal", "stop"):
        out["exitRule"] = raw["exitRule"]
    return out


def renormalize_overlay(bt_curve: list[dict], start: str) -> list[dict] | None:
    """回测曲线截到模拟盘起点之后并归一到 1.0——两条线同段可比，
    分叉程度即过拟合读数。重叠不足 2 个点时不叠加。"""
    seg = [p for p in bt_curve if p.get("t", "") >= start]
    if len(seg) < 2 or not seg[0].get("v"):
        return None
    base = seg[0]["v"]
    return [{"t": p["t"], "v": round(p["v"] / base, 4)} for p in seg]


def _one_word(bar: Bar, prev_close: float, pct: float, direction: int) -> bool:
    """当日是否一字板（同 backtest._one_word 的判定，输入换成单日 bar + 前收）。"""
    o, high, low, close = bar
    if not prev_close or prev_close <= 0 or abs(high - low) > 1e-9:
        return False
    limit_px = prev_close * (1 + pct * direction)
    return abs(close - limit_px) <= limit_px * 0.004


def _stop_hit(bar: Bar, entry_px: float, stop_gain: float, stop_loss: float
              ) -> tuple[float, str] | None:
    """当日止盈止损触发（同 backtest._stop_exit 的单日判定次序）。"""
    o, high, low, _close = bar
    gain_px = entry_px * (1 + stop_gain / 100)
    loss_px = entry_px * (1 - stop_loss / 100)
    if o <= loss_px:
        return o, "stop_loss"
    if o >= gain_px:
        return o, "stop_gain"
    if low <= loss_px:
        return loss_px, "stop_loss"
    if high >= gain_px:
        return gain_px, "stop_gain"
    return None


def _bars_on(db: Session, codes: set[str], day: datetime) -> dict[str, Bar]:
    if not codes:
        return {}
    rows = db.execute(
        select(Kline.code, Kline.open, Kline.high, Kline.low, Kline.close).where(
            Kline.period == "1d", Kline.ts == day, Kline.code.in_(sorted(codes))
        )
    )
    return {code: (o, h, lo, c) for code, o, h, lo, c in rows}


def _rev_signal_today(db: Session, code: str, day: datetime,
                      rev: list[dict[str, Any]]) -> bool:
    """反向信号是否在 day 当日成立（取该股截至 day 的近端 K 线）。"""
    need = technical.bars_needed(rev) + 1
    rows = db.execute(
        select(Kline.close, Kline.open)
        .where(Kline.period == "1d", Kline.code == code, Kline.ts <= day)
        .order_by(Kline.ts.desc())
        .limit(need)
    ).all()
    closes = [r[0] for r in reversed(rows)]
    opens = [r[1] for r in reversed(rows)]
    return bool(closes) and technical.passes(rev, closes, None, opens)


def _calendar(db: Session, after: datetime | None, upto: datetime) -> list[datetime]:
    stmt = select(Kline.ts).where(
        Kline.period == "1d", Kline.code == _CAL_REF, Kline.ts <= upto
    )
    if after is not None:
        stmt = stmt.where(Kline.ts > after)
    return sorted({r[0] for r in db.execute(stmt)})


def settle_all(db: Session) -> int:
    """结算全部启用账户到最新交易日；返回结算的 (账户, 日) 次数。幂等。"""
    latest = db.execute(
        select(Kline.ts).where(Kline.period == "1d").order_by(Kline.ts.desc()).limit(1)
    ).scalar()
    if latest is None:
        return 0
    accounts = db.execute(
        select(PaperAccount).where(PaperAccount.enabled.is_(True))
    ).scalars().all()
    settled = 0
    for acct in accounts:
        if acct.last_settled is None:
            days = [latest]  # 新账户：当日启动（收今日信号，明日开盘首买）
        else:
            days = _calendar(db, acct.last_settled, latest)
        for d in days:
            try:
                _settle_day(db, acct, d)
                settled += 1
            except Exception:  # noqa: BLE001 — 单账户异常不拦全场
                logger.exception("paper settle failed: acct %s @ %s", acct.id, d)
                db.rollback()
                break
    return settled


def _settle_day(db: Session, acct: PaperAccount, day: datetime) -> None:
    if db.get(PaperEquity, (acct.id, day)) is not None:
        acct.last_settled = max(acct.last_settled or day, day)
        db.commit()
        return
    p = clean_params(acct.params)
    rate = p["costRate"]
    strategy = db.get(Strategy, acct.strategy_id)
    run = db.execute(
        select(StrategyRun).where(
            StrategyRun.strategy_id == acct.strategy_id, StrategyRun.run_date == day
        )
    ).scalar()

    positions = db.execute(
        select(PaperPosition).where(PaperPosition.account_id == acct.id)
    ).scalars().all()
    pending = sorted([x for x in positions if x.status == "pending"], key=lambda x: x.code)
    open_pos = [x for x in positions if x.status == "open"]
    stats = dict(acct.stats or {})
    active_codes = {x.code for x in positions}  # 在途代码（含排队），单股同时只持一笔

    codes = {x.code for x in positions} | set((run.hit_codes if run else []) or [])
    bars = _bars_on(db, codes, day)

    # ---- 入场（今日开盘）：容量按开盘时点算，当日退出的仓位收盘才腾出 ----
    entered_today: set[int] = set()
    for pos in pending:
        if pos.signal_date >= day:
            continue  # 今日新信号明天才入场
        bar = bars.get(pos.code)
        if bar is None or _one_word(bar, pos.last_price, _limit_pct(pos.code, pos.name), +1):
            pos.defer_days += 1
            if bar is not None:
                pos.last_price = bar[3]
            if pos.defer_days >= _ENTRY_DEFER_MAX:
                stats["skippedByLimit"] = stats.get("skippedByLimit", 0) + 1
                active_codes.discard(pos.code)
                db.delete(pos)
            continue
        if len(open_pos) >= p["maxConcurrent"] or bar[0] <= 0:
            stats["skippedByCapacity"] = stats.get("skippedByCapacity", 0) + 1
            active_codes.discard(pos.code)
            db.delete(pos)
            continue
        pos.status = "open"
        pos.entry_date = day
        pos.entry_px = bar[0]
        pos.hold_days = 0
        open_pos.append(pos)
        entered_today.add(pos.id)

    # ---- 退出判定 + 逐仓日收益 ----
    rev = (
        technical.reverse_signal((strategy.dsl or {}).get("technical") or [])
        if p["exitRule"] == "signal" and strategy
        else None
    )
    day_ret = 0.0
    for pos in list(open_pos):
        bar = bars.get(pos.code)
        if bar is None:
            continue  # 停牌：价格与持有日计数都冻结
        is_entry_day = pos.id in entered_today
        prev = pos.entry_px if is_entry_day else pos.last_price
        if not is_entry_day:
            pos.hold_days += 1
        pct = _limit_pct(pos.code, pos.name)

        exit_px: float | None = None
        reason = ""
        if pos.exit_pending:
            pos.exit_defer += 1
            if not _one_word(bar, prev, pct, -1) or pos.exit_defer >= _EXIT_DEFER_MAX:
                exit_px, reason = bar[3], "hold"  # 顺延后按收盘强平（触发价已失效）
        elif p["exitRule"] == "stop":
            hit = _stop_hit(bar, pos.entry_px, p["stopGain"], p["stopLoss"])
            if hit:
                exit_px, reason = hit
            elif pos.hold_days >= p["holdDays"]:  # 与回测一致：stop 模式也有持有期兜底
                exit_px, reason = bar[3], "hold"
        elif p["exitRule"] == "signal":
            cap = p["holdDays"] * 3
            if rev and not is_entry_day and _rev_signal_today(db, pos.code, day, rev):
                exit_px, reason = bar[3], "signal"
            elif pos.hold_days >= cap:
                exit_px, reason = bar[3], "hold"
        else:  # hold
            if pos.hold_days >= p["holdDays"]:
                exit_px, reason = bar[3], "hold"

        # 触发了退出但当日一字跌停卖不出 → 转入顺延
        if exit_px is not None and not pos.exit_pending and _one_word(bar, prev, pct, -1):
            pos.exit_pending = True
            pos.exit_defer = 0
            exit_px = None

        if exit_px is not None:
            if is_entry_day:
                day_ret += exit_px / pos.entry_px - 1 - 2 * rate
            else:
                day_ret += exit_px / prev - 1 - rate
            db.add(PaperTrade(
                account_id=acct.id, code=pos.code, name=pos.name,
                signal_date=pos.signal_date, entry_date=pos.entry_date or day,
                entry_px=round(pos.entry_px, 3), exit_date=day, exit_px=round(exit_px, 3),
                ret=round((exit_px / pos.entry_px - 1 - 2 * rate) * 100, 2),
                reason=reason,
            ))
            active_codes.discard(pos.code)
            db.delete(pos)
            open_pos.remove(pos)
        else:
            day_ret += bar[3] / prev - 1 - (rate if is_entry_day else 0.0)
            pos.last_price = bar[3]

    r = day_ret / p["maxConcurrent"]
    acct.equity = round(acct.equity * (1 + r), 6)
    acct.stats = stats
    acct.last_settled = day
    db.add(PaperEquity(
        account_id=acct.id, date=day, equity=acct.equity,
        daily_return=round(r * 100, 4), positions=len(open_pos),
    ))

    # ---- 今日新信号入队（明日开盘入场）：单股同时只持一笔，按代码序限队 ----
    if run and (run.hit_codes or []):
        quota = p["maxConcurrent"] * _PENDING_CAP_X - len(active_codes)
        fresh = [c for c in sorted(run.hit_codes) if c not in active_codes][: max(quota, 0)]
        names = _stock_names(db, fresh)
        for code in fresh:
            bar = bars.get(code)
            if bar is None or not bar[3]:
                continue  # 当日无 K 线（停牌/新数据缺失）不入队
            db.add(PaperPosition(
                account_id=acct.id, code=code, name=names.get(code, code),
                status="pending", signal_date=day, last_price=bar[3],
            ))
            active_codes.add(code)
    db.commit()


def _stock_names(db: Session, codes: list[str]) -> dict[str, str]:
    if not codes:
        return {}
    from app.models.market import StockInfo

    return {
        s.code: s.name
        for s in db.execute(select(StockInfo).where(StockInfo.code.in_(codes))).scalars()
    }
