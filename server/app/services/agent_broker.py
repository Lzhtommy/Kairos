"""AI 操盘手的模拟券商：下单校验、按日 K 撮合、逐日结算（股数 + 现金真实记账）。

成交口径：
- 撮合时点：created_at 早于交易日 D 09:30（CST）的 pending 单参与 D 的撮合，盘后按 D 的日 K 结算；
  D 结束仍未成交的单作废（当日有效）。先卖后买，当日买入的股票最早下一个交易日的单才能卖（T+1）；
- 市价单按开盘价 ± 滑点成交；限价买单 low ≤ 限价才成交，价格 min(开盘, 限价)，卖单对称；
- 一字涨停买不进、一字跌停卖不出（判定与回测 / 策略模拟盘一致）；停牌不成交；
  单笔成交量不超过当日成交量的 5%；
- 买入：主板 / 创业板 100 股整数倍；科创板 ≥200 股、1 股递增；北交所 ≥100 股、1 股递增。
  卖出零股只能一次性卖完；
- 费用：佣金 万 2.5（最低 5 元，双向）+ 印花税 0.05%（仅卖出）+ 过户费 0.001%（沪深双向），
  另按 slippageBps 计滑点；
- 除权除息：日 K 是前复权口径，除权日持仓市值会"假跌"。按分红表（dividend）入账现金红利
  （差别化红利税：持有 ≤1 月 20%、≤1 年 10%、>1 年免）与送转股；分红表没有记录时按
  复权比例折算股数，保持市值连续。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import AgentAccount, AgentEquity, AgentOrder, AgentPosition
from app.models.market import AdjustmentEvent, IndexQuote, Kline, Quote, StockInfo
from app.models.warehouse import Dividend, IndexBar
from app.services.backtest import _limit_pct

logger = logging.getLogger("kairos.agent")

DEFAULT_FEES: dict[str, float] = {
    "commission": 0.00025,  # 佣金率（双向）
    "minCommission": 5.0,  # 单笔最低佣金，元
    "stampDuty": 0.0005,  # 印花税（仅卖出）
    "transferFee": 0.00001,  # 过户费（沪深双向，北交所无）
    "slippageBps": 5.0,  # 市价单滑点，基点
}
_VOLUME_CAP = 0.05  # 单笔成交不超过当日成交量的比例
_CAL_REF = "600519"  # 交易日历参照股（与 paper / backtest 一致）
_CLOSE_FINAL = (15, 10)  # CST 15:10 之后当日 K 线才是收盘口径


def fees_of(acct: AgentAccount) -> dict[str, float]:
    return {**DEFAULT_FEES, **(acct.fees or {})}


def is_bse(code: str) -> bool:
    return code.startswith(("4", "8", "92"))


def lot_rule(code: str) -> tuple[int, int]:
    """(最小买入股数, 递增单位)。"""
    if code.startswith("688"):
        return 200, 1
    if is_bse(code):
        return 100, 1
    return 100, 100


def trade_fee(fees: dict[str, float], side: str, code: str, amount: float) -> float:
    if amount <= 0:
        return 0.0
    fee = max(amount * fees["commission"], fees["minCommission"])
    if not is_bse(code):
        fee += amount * fees["transferFee"]
    if side == "sell":
        fee += amount * fees["stampDuty"]
    return round(fee, 2)


def cst_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=8)


def cst_today() -> datetime:
    d = cst_now().date()
    return datetime(d.year, d.month, d.day)


def _open_cutoff(day: datetime) -> datetime:
    """交易日 day 的 09:30 CST（UTC aware），早于它创建的单参与当日撮合。"""
    return datetime(day.year, day.month, day.day, 1, 30, tzinfo=timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite 读回来是 naive UTC


# ---------------------------------------------------------------- 查询辅助


def latest_prices(db: Session, codes: list[str]) -> dict[str, float]:
    """最新可得价格：实时快照优先，没有就取最近收盘。"""
    if not codes:
        return {}
    out = {q.code: q.price for q in db.execute(select(Quote).where(Quote.code.in_(codes))).scalars() if q.price}
    for code in set(codes) - set(out):
        px = db.execute(
            select(Kline.close).where(Kline.code == code, Kline.period == "1d").order_by(Kline.ts.desc()).limit(1)
        ).scalar()
        if px:
            out[code] = px
    return out


def _bar(db: Session, code: str, day: datetime) -> tuple[float, float, float, float, int] | None:
    row = db.execute(
        select(Kline.open, Kline.high, Kline.low, Kline.close, Kline.volume).where(
            Kline.code == code, Kline.period == "1d", Kline.ts == day
        )
    ).first()
    return tuple(row) if row else None  # type: ignore[return-value]


def _prev_close(db: Session, code: str, day: datetime) -> float | None:
    return db.execute(
        select(Kline.close)
        .where(Kline.code == code, Kline.period == "1d", Kline.ts < day)
        .order_by(Kline.ts.desc())
        .limit(1)
    ).scalar()


def _one_word(bar: tuple, prev_close: float | None, pct: float, direction: int) -> bool:
    _o, high, low, close, _v = bar
    if not prev_close or prev_close <= 0 or abs(high - low) > 1e-9:
        return False
    limit_px = prev_close * (1 + pct * direction)
    return abs(close - limit_px) <= limit_px * 0.004


def _stock(db: Session, code: str) -> StockInfo | None:
    return db.get(StockInfo, code)


def calendar(db: Session, after: datetime | None, upto: datetime) -> list[datetime]:
    stmt = select(Kline.ts).where(Kline.period == "1d", Kline.code == _CAL_REF, Kline.ts <= upto)
    if after is not None:
        stmt = stmt.where(Kline.ts > after)
    return sorted({r[0] for r in db.execute(stmt)})


# ---------------------------------------------------------------- 下单


def reserved(db: Session, acct_id: int) -> tuple[float, dict[str, int]]:
    """在途挂单占用：(买单预估占用资金, 各股票卖单占用股数)。"""
    pending = db.execute(
        select(AgentOrder).where(AgentOrder.account_id == acct_id, AgentOrder.status == "pending")
    ).scalars().all()
    buy_codes = [o.code for o in pending if o.side == "buy" and o.limit_price is None]
    px = latest_prices(db, buy_codes)
    cash = 0.0
    sells: dict[str, int] = {}
    for o in pending:
        if o.side == "buy":
            price = o.limit_price or px.get(o.code, 0.0)
            cash += o.shares * price * 1.003  # 留出费用 + 跳空余量
        else:
            sells[o.code] = sells.get(o.code, 0) + o.shares
    return cash, sells


def place_order(
    db: Session,
    acct: AgentAccount,
    *,
    code: str,
    side: str,
    shares: int,
    limit_price: float | None,
    reason: str,
    memory_ids: list[int] | None = None,
    session_id: int | None = None,
) -> AgentOrder:
    """校验并落一笔 pending 单；校验失败抛 ValueError（文案直接给 agent 看）。"""
    code = (code or "").strip()
    if side not in ("buy", "sell"):
        raise ValueError("side 只能是 buy 或 sell")
    info = _stock(db, code)
    if info is None:
        raise ValueError(f"找不到股票 {code}，先用 search_stocks 确认代码")
    if info.delist_date is not None:
        raise ValueError(f"{code} 已退市")
    try:
        shares = int(shares)
    except (TypeError, ValueError):
        raise ValueError("shares 必须是整数股数") from None
    if shares <= 0:
        raise ValueError("shares 必须为正")
    if limit_price is not None and limit_price <= 0:
        raise ValueError("limit_price 必须为正，市价单不传")

    if side == "buy":
        min_qty, step = lot_rule(code)
        if shares < min_qty or (shares - min_qty) % step:
            raise ValueError(f"{code} 买入须 ≥{min_qty} 股且按 {step} 股递增")
        price = limit_price or latest_prices(db, [code]).get(code)
        if not price:
            raise ValueError(f"{code} 暂无价格，无法估算买入资金")
        held_cash, _ = reserved(db, acct.id)
        need = shares * price * 1.003
        free = acct.cash - held_cash
        if need > free:
            raise ValueError(f"可用资金不足：预计需 {need:,.0f} 元，可用 {free:,.0f} 元（已扣在途买单）")
    else:
        pos = db.get(AgentPosition, (acct.id, code))
        held = pos.shares if pos else 0
        _, sells = reserved(db, acct.id)
        avail = held - sells.get(code, 0)
        if shares > avail:
            raise ValueError(f"{code} 可卖 {avail} 股（持仓 {held}，在途卖单 {sells.get(code, 0)}）")
        min_qty, step = lot_rule(code)
        if shares != avail and shares % 100 and step == 100:
            raise ValueError("卖出须为 100 股整数倍，零股只能一次性全部卖出")

    order = AgentOrder(
        account_id=acct.id, session_id=session_id, code=code, name=info.name, side=side, shares=shares,
        limit_price=round(limit_price, 3) if limit_price else None, reason=(reason or "")[:2000],
        memory_ids=[int(m) for m in (memory_ids or []) if str(m).isdigit()][:10],
    )
    db.add(order)
    db.commit()
    return order


# ---------------------------------------------------------------- 结算


def settle_due(db: Session, acct: AgentAccount) -> list[datetime]:
    """把账户逐日结算到最新一个收盘口径的交易日；返回本次结算的交易日。幂等，停机后自动补日。"""
    now = cst_now()
    upto = cst_today()
    if (now.hour, now.minute) < _CLOSE_FINAL:
        upto -= timedelta(days=1)  # 盘中当日 bar 还在滚动
    if acct.last_settled is None:
        created = _aware(acct.created_at) + timedelta(hours=8)
        start = datetime(created.year, created.month, created.day) - timedelta(days=1)
    else:
        start = acct.last_settled
    days = calendar(db, start, upto)
    done = []
    for d in days:
        settle_day(db, acct, d)
        done.append(d)
    return done


def settle_day(db: Session, acct: AgentAccount, day: datetime) -> dict[str, Any]:
    """撮合 day 当日的订单并按收盘价盯市，写一条 agent_equity。返回当日结算摘要。"""
    if db.get(AgentEquity, (acct.id, day)) is not None:
        acct.last_settled = max(acct.last_settled or day, day)
        db.commit()
        return {}
    fees = fees_of(acct)
    slip = fees["slippageBps"] / 10000
    notes: list[str] = _corporate_actions(db, acct, day)
    day_fees = 0.0

    cutoff = _open_cutoff(day)
    orders = [
        o for o in db.execute(
            select(AgentOrder).where(AgentOrder.account_id == acct.id, AgentOrder.status == "pending")
            .order_by(AgentOrder.id)
        ).scalars()
        if _aware(o.created_at) < cutoff
    ]
    orders.sort(key=lambda o: (o.side != "sell", o.id))  # 先卖后买：卖出回笼的资金当日可用

    for o in orders:
        o.trade_date = day
        bar = _bar(db, o.code, day)
        if bar is None:
            o.status, o.note = "expired", "停牌或当日无行情，未成交"
            continue
        open_px, high, low, _close, volume = bar
        prev = _prev_close(db, o.code, day)
        info = _stock(db, o.code)
        pct = _limit_pct(o.code, bool(info and "ST" in info.name), day.date())
        vol_cap = int(volume * _VOLUME_CAP)
        min_qty, step = lot_rule(o.code)

        if o.side == "buy":
            if _one_word(bar, prev, pct, +1):
                o.status, o.note = "expired", "一字涨停，未能买入"
                continue
            if o.limit_price is None:
                px = open_px * (1 + slip)
            elif low <= o.limit_price:
                px = min(open_px, o.limit_price)
            else:
                o.status, o.note = "expired", f"最低价 {low:.2f} 未触及限价 {o.limit_price:.2f}"
                continue
            qty = min(o.shares, vol_cap)
            qty = qty if qty == o.shares else _floor_lot(qty, min_qty, step)
            while qty >= min_qty and qty * px + trade_fee(fees, "buy", o.code, qty * px) > acct.cash:
                qty = _floor_lot(qty - step, min_qty, step)
            if qty < min_qty:
                o.status, o.note = "expired", "资金不足或超过当日成交量上限，未成交"
                continue
            amount = qty * px
            fee = trade_fee(fees, "buy", o.code, amount)
            acct.cash -= amount + fee
            pos = db.get(AgentPosition, (acct.id, o.code))
            if pos is None:
                pos = AgentPosition(account_id=acct.id, code=o.code, name=o.name, shares=0, cost=0.0, opened_at=day)
                db.add(pos)
            pos.shares += qty
            pos.cost += amount + fee
            pos.last_price = px
            _fill(o, qty, px, amount, fee)
            if qty < o.shares:
                o.note = f"部分成交 {qty}/{o.shares} 股（资金或成交量限制），余量作废"
        else:
            pos = db.get(AgentPosition, (acct.id, o.code))
            if pos is None or pos.shares <= 0:
                o.status, o.note = "expired", "无可卖持仓"
                continue
            if _one_word(bar, prev, pct, -1):
                o.status, o.note = "expired", "一字跌停，未能卖出"
                continue
            if o.limit_price is None:
                px = open_px * (1 - slip)
            elif high >= o.limit_price:
                px = max(open_px, o.limit_price)
            else:
                o.status, o.note = "expired", f"最高价 {high:.2f} 未触及限价 {o.limit_price:.2f}"
                continue
            qty = min(o.shares, pos.shares, vol_cap)
            if qty < pos.shares and step == 100:
                qty -= qty % 100
            if qty <= 0:
                o.status, o.note = "expired", "超过当日成交量上限，未成交"
                continue
            amount = qty * px
            fee = trade_fee(fees, "sell", o.code, amount)
            cost_part = pos.cost * qty / pos.shares
            acct.cash += amount - fee
            pos.cost -= cost_part
            pos.shares -= qty
            _fill(o, qty, px, amount, fee)
            o.realized_pnl = round(amount - fee - cost_part, 2)
            if qty < o.shares:
                o.note = f"部分成交 {qty}/{o.shares} 股，余量作废"
            if pos.shares <= 0:
                db.delete(pos)
        day_fees += o.fee

    # ---- 盯市 ----
    db.flush()
    positions = db.execute(select(AgentPosition).where(AgentPosition.account_id == acct.id)).scalars().all()
    mv = 0.0
    for pos in positions:
        bar = _bar(db, pos.code, day)
        if bar is not None:
            pos.last_price = bar[3]
        mv += pos.shares * pos.last_price
    prev_eq = db.execute(
        select(AgentEquity.total).where(AgentEquity.account_id == acct.id, AgentEquity.date < day)
        .order_by(AgentEquity.date.desc()).limit(1)
    ).scalar() or acct.initial_cash
    total = acct.cash + mv
    acct.cash = round(acct.cash, 2)
    db.add(AgentEquity(
        account_id=acct.id, date=day, cash=acct.cash, market_value=round(mv, 2), total=round(total, 2),
        daily_return=round((total / prev_eq - 1) * 100, 4) if prev_eq else 0.0,
        bench_close=_bench_close(db, acct.benchmark, day), fees=round(day_fees, 2),
    ))
    acct.last_settled = day
    db.commit()
    filled = [o for o in orders if o.status == "filled"]
    return {
        "date": day.strftime("%Y-%m-%d"),
        "filled": len(filled),
        "expired": len(orders) - len(filled),
        "fees": round(day_fees, 2),
        "total": round(total, 2),
        "notes": notes,
    }


def _fill(o: AgentOrder, qty: int, px: float, amount: float, fee: float) -> None:
    o.status = "filled"
    o.filled_shares = qty
    o.fill_price = round(px, 3)
    o.amount = round(amount, 2)
    o.fee = fee


def _floor_lot(qty: int, min_qty: int, step: int) -> int:
    if qty < min_qty:
        return 0
    return min_qty + (qty - min_qty) // step * step


def _bench_close(db: Session, code: str, day: datetime) -> float | None:
    px = db.execute(
        select(IndexBar.close).where(IndexBar.index_code == code, IndexBar.trade_date == day.date())
    ).scalar()
    if px:
        return px
    q = db.get(IndexQuote, code)  # 指数日线 18:00 才入库，盘后结算当天用实时快照
    if q is not None and q.ts is not None and (_aware(q.ts) + timedelta(hours=8)).date() == day.date():
        return q.price
    return None


def _corporate_actions(db: Session, acct: AgentAccount, day: datetime) -> list[str]:
    """除权除息日处理持仓：现金红利入账（扣差别化红利税）+ 送转股；无分红记录按复权比例折算。"""
    positions = db.execute(select(AgentPosition).where(AgentPosition.account_id == acct.id)).scalars().all()
    if not positions:
        return []
    codes = [p.code for p in positions]
    divs = {
        d.code: d for d in db.execute(
            select(Dividend).where(Dividend.code.in_(codes), Dividend.ex_date == day.date())
        ).scalars()
    }
    adjs = {
        a.code: a.ratio for a in db.execute(
            select(AdjustmentEvent).where(AdjustmentEvent.code.in_(codes), AdjustmentEvent.date == day)
        ).scalars()
    }
    notes = []
    for pos in positions:
        d = divs.get(pos.code)
        if d is not None and (d.cash_per10 or d.bonus_per10 or d.transfer_per10):
            held_days = (day - pos.opened_at).days
            tax = 0.2 if held_days <= 30 else 0.1 if held_days <= 365 else 0.0
            cash = pos.shares * (d.cash_per10 or 0) / 10 * (1 - tax)
            extra = int(pos.shares * ((d.bonus_per10 or 0) + (d.transfer_per10 or 0)) / 10)
            acct.cash += cash
            pos.shares += extra
            notes.append(f"{pos.name} 除权除息：红利 {cash:,.2f} 元（税率 {tax:.0%}），送转 {extra} 股")
        elif pos.code in adjs and 0 < adjs[pos.code] < 1:
            old = pos.shares
            pos.shares = int(round(old / adjs[pos.code]))
            notes.append(f"{pos.name} 除权（无分红明细）：按复权比例 {adjs[pos.code]:.4f} 折算 {old}→{pos.shares} 股")
    return notes
