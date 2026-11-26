"""Data collector: seeds reference data once, refreshes quotes/indices each tick.

Runs every `collect_interval_seconds` via APScheduler (see main.py). With a
live provider (akshare/tencent) it only collects during A-share trading
sessions; with the seed provider it always refreshes so the demo keeps moving.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from time import sleep

from sqlalchemy import delete, func, insert, select, update

from app.core.db import SessionLocal
from kairos_data.store import upsert_rows
from app.models.market import (
    AdjustmentEvent,
    Fundamental,
    IndexQuote,
    Kline,
    Quote,
    StockInfo,
)
from app.models.warehouse import DailyBasic
from app.providers.factory import get_provider
from app.services.market import recent_cutoff

logger = logging.getLogger("kairos.collector")

# China Standard Time = UTC+8; sessions 09:25–11:30 and 13:00–15:00 —
# 早盘从 9:25 开始：集合竞价撮合已结束，行情源已给出开盘价。
_CST_OFFSET_HOURS = 8

# 日 K 目标深度（约 3 年交易日），供 bootstrap / 回补 / 加深共用。
KLINE_DEPTH = 760


def in_trading_session(now_utc: datetime | None = None) -> bool:
    now = now_utc or datetime.now(timezone.utc)
    cst_hour = (now.hour + _CST_OFFSET_HOURS) % 24
    t = time(cst_hour, now.minute)
    if now.weekday() >= 5:  # Sat/Sun
        return False
    return (time(9, 25) <= t <= time(11, 30)) or (time(13, 0) <= t <= time(15, 0))


# 除权检测当日已完成的标记（CST 日期字符串）。重启后重跑无害：
# 历史已重标定 → 比例重算 ≈ 1，且事件表 (code, date) 去重
_adj_done_day: str | None = None

# 复权比例的合法区间：分红/送转/配股都是下调（10送10 → 0.5），
# 上调（缩股）极罕见不处理；|r-1| ≤ 0.1% 视为正常价差不动
_ADJ_MIN, _ADJ_EPS = 0.2, 0.001


def _apply_adjustments(db, quotes, day_start: datetime) -> int:
    """除权除息当日检测 + 历史重标定，维持全库"连续前复权"口径。

    交易所披露的 prev_close 与库里昨收出现超阈值下调 → 除权事件：
    把该股全部历史 bar 按比例缩放（假跌恰好被抵消，任意两日比价关系
    保持正确），并同步缩放模拟盘持仓的成本价/最新价。读路径零改动。

    已知边界：停机跨过 ex-date 时，比例里会混入当日真实涨跌（误差一次性、幅度小）；
    低于 0.1% 的小额分红不触发。两者都会累积成与数据源当前前复权价的小幅偏差
    （2026-11 抽查 14 只，两年前的价格最多差 2.1%）。目前没有定期整段重刷来兜底，
    需要时用 kairos-data kline / scripts/backfill_kline 重拉。
    """
    todays = {
        q.code: q for q in quotes if q.exchange_ts and q.exchange_ts.date() == day_start.date()
    }
    if not todays:
        return 0
    # 每股最新一根 ts < today 的收盘（库里口径的"昨收"）
    rn = func.row_number().over(partition_by=Kline.code, order_by=Kline.ts.desc()).label("rn")
    sub = select(Kline.code, Kline.close, rn).where(
        Kline.period == "1d", Kline.ts < day_start, Kline.code.in_(sorted(todays))
    )
    if (cutoff := recent_cutoff(db, 1)) is not None:
        sub = sub.where(Kline.ts >= cutoff)
    sub = sub.subquery()
    prev_stored = {
        code: close for code, close in db.execute(
            select(sub.c.code, sub.c.close).where(sub.c.rn == 1)
        )
    }
    seen_today = {
        r[0] for r in db.execute(
            select(AdjustmentEvent.code).where(AdjustmentEvent.date == day_start)
        )
    }
    applied = 0
    for code, q in todays.items():
        stored = prev_stored.get(code)
        if code in seen_today or not stored or stored <= 0 or q.prev_close <= 0:
            continue
        r = q.prev_close / stored
        if not (_ADJ_MIN <= r <= 1.0) or abs(r - 1) <= _ADJ_EPS:
            continue
        db.execute(
            update(Kline)
            .where(Kline.code == code, Kline.period == "1d", Kline.ts < day_start)
            .values(open=Kline.open * r, high=Kline.high * r,
                    low=Kline.low * r, close=Kline.close * r)
        )
        # 模拟盘持仓与排队的价格基准同口径缩放（否则止损阈值/浮盈会按假跌算）
        from app.models.strategy import PaperPosition

        db.execute(
            update(PaperPosition)
            .where(PaperPosition.code == code)
            .values(entry_px=PaperPosition.entry_px * r,
                    last_price=PaperPosition.last_price * r)
        )
        db.add(AdjustmentEvent(code=code, date=day_start, ratio=round(r, 6)))
        applied += 1
        logger.info("Adjustment %s: ratio %.4f (prev %.3f -> %.3f)",
                    code, r, stored, q.prev_close)
    if applied:
        db.commit()
    return applied


def _refresh_quotes(db) -> int:
    provider = get_provider()
    quotes = provider.get_quotes()

    # 除权检测每个交易日只跑一次（首采时），seed 演示数据不适用
    global _adj_done_day
    cst_today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
    if provider.name != "seed" and _adj_done_day != str(cst_today):
        day_start = datetime(cst_today.year, cst_today.month, cst_today.day)
        try:
            _apply_adjustments(db, quotes, day_start)
        except Exception:  # noqa: BLE001 — 检测失败不拦行情写入
            logger.exception("adjustment detection failed")
            db.rollback()
        _adj_done_day = str(cst_today)

    fund_map = {f.code: f for f in db.execute(select(Fundamental)).scalars().all()}
    # 同一代码在一批里出现两次时 upsert 取后者，与原 merge 语义一致
    rows = {
        q.code: {
            "code": q.code,
            "ts": q.ts or datetime.now(timezone.utc),
            "price": q.price,
            "prev_close": q.prev_close,
            "open": q.open,
            "high": q.high,
            "low": q.low,
            "volume": q.volume,
            "turnover": q.turnover,
            "turnover_rate": q.turnover_rate,
            "pe": q.pe,
            "pb": q.pb,
            "market_cap": q.market_cap,
            "roe": q.roe or (fund_map[q.code].roe if q.code in fund_map else 0.0),
        }
        for q in quotes
    }
    upsert_rows(db, Quote, list(rows.values()))
    for idx in provider.get_indices():
        db.merge(
            IndexQuote(
                code=idx.code,
                name=idx.name,
                ts=datetime.now(timezone.utc),
                price=idx.price,
                change=idx.change,
                change_pct=idx.change_pct,
            )
        )
    _upsert_today_bars(db, quotes)
    db.commit()
    return len(quotes)


def _upsert_today_bars(db, quotes) -> None:
    """把当日行情合成/覆盖为当日日 K bar，让 K 线保持到最新交易日。

    日 K 只在 bootstrap 时批量拉过一次，之后全靠这里逐分钟刷新当日 bar
    （收盘后最后一次刷新即当日收盘 bar）。只处理交易所时间戳落在今天的
    行情——节假日/停牌时腾讯返回旧数据，不会被误写成今天的 bar。
    """
    cst_today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
    todays = [q for q in quotes if q.exchange_ts and q.exchange_ts.date() == cst_today]
    if not todays:
        return
    day_start = datetime(cst_today.year, cst_today.month, cst_today.day)
    db.execute(delete(Kline).where(Kline.period == "1d", Kline.ts == day_start))
    db.execute(insert(Kline), [
        {"code": q.code, "period": "1d", "ts": day_start, "open": q.open,
         "high": q.high, "low": q.low, "close": q.price, "volume": q.volume * 100}
        for q in todays
    ])


def _insert_bars(db, code: str, candles) -> None:
    """日 K 批量 upsert（主键 code, period, ts）：数据源偶尔回重复日期，冲突时以新值为准。"""
    if not candles:
        return
    upsert_rows(db, Kline, [
        {"code": code, "period": "1d", "ts": c.ts, "open": c.open, "high": c.high,
         "low": c.low, "close": c.close, "volume": c.volume * 100}
        for c in candles
    ])


def bootstrap(db) -> None:
    """Populate reference data (universe, klines, fundamentals) if empty."""
    provider = get_provider()

    if db.execute(select(func.count()).select_from(StockInfo)).scalar() == 0:
        for meta in provider.get_universe():
            db.merge(
                StockInfo(code=meta.code, name=meta.name, market=meta.market, industry=meta.industry)
            )
        db.commit()
        logger.info("Seeded stock universe")

    if db.execute(select(func.count()).select_from(Fundamental)).scalar() == 0:
        for f in provider.get_fundamentals():
            db.merge(
                Fundamental(
                    code=f.code, pe=f.pe, pb=f.pb, roe=f.roe,
                    market_cap=f.market_cap, dividend_yield=f.dividend_yield,
                )
            )
        db.commit()
        logger.info("Seeded fundamentals")

    if db.execute(select(func.count()).select_from(Kline)).scalar() == 0:
        codes = [m.code for m in provider.get_universe()]
        failed = 0
        for i, code in enumerate(codes, 1):
            sleep(0.05)  # pace the burst — thousands of rapid calls trip provider rate limits
            try:
                candles = provider.get_kline(code, "1d", KLINE_DEPTH)
            except Exception as exc:  # noqa: BLE001 — one bad ticker must not kill startup
                logger.warning("Kline fetch failed for %s: %s", code, exc)
                failed += 1
                continue
            _insert_bars(db, code, candles)
            if i % 200 == 0:
                db.commit()  # bound the transaction; keep partial progress on crash
                logger.info("Seeded K-lines: %d/%d", i, len(codes))
        db.commit()
        logger.info("Seeded K-lines for %d stocks (%d failed)", len(codes) - failed, failed)


def refresh_reference() -> None:
    """Refresh slow-moving reference data: industry classification + fundamentals
    (roe / dividend_yield). Runs in the scheduler right after startup and daily —
    bootstrap only seeds empty tables, so existing DBs rely on this to heal the
    placeholder "—" / 0.0 values."""
    provider = get_provider()
    if provider.name == "seed":
        return
    db = SessionLocal()
    try:
        metas = provider.get_universe(refresh=True)
        n_ind = 0
        # 先整表载入身份映射，下面的 db.get 就不再逐只往返数据库
        db.execute(select(StockInfo)).scalars().all()
        for meta in metas:
            info = db.get(StockInfo, meta.code)
            if info is None:
                # 新上市股票即使还没行业分类也要入库，否则前端只能显示裸代码
                db.add(StockInfo(code=meta.code, name=meta.name, market=meta.market, industry=meta.industry))
            elif meta.industry != "—" and info.industry != meta.industry:
                info.industry = meta.industry
            if meta.industry != "—":
                n_ind += 1
        funds = {
            f.code: {"code": f.code, "pe": f.pe, "pb": f.pb, "roe": f.roe,
                     "market_cap": f.market_cap, "dividend_yield": f.dividend_yield}
            for f in provider.get_fundamentals()
        }
        db.flush()  # 新增的 StockInfo 先落库，和下面的核心语句同一事务
        upsert_rows(db, Fundamental, list(funds.values()))
        db.commit()
        pruned, renamed, delisted = _prune_stale(db, {m.code for m in metas})
        if pruned or renamed or delisted:
            logger.info("Stale cleanup: %d quotes removed, %d renumbered BJ codes dropped, "
                        "%d BJ stocks marked delisted", pruned, renamed, delisted)

        # 给 bootstrap 之后新上市的股票回补日 K（没有 K 线的股票会让回测缺数据）。
        # 每轮最多补 300 只，既覆盖日常新增，又不至于在数据大面积缺失时打爆限流。
        have_kline = {
            r[0] for r in db.execute(select(Kline.code).where(Kline.period == "1d").distinct())
        }
        missing = [m.code for m in metas if m.code not in have_kline][:300]
        backfilled = 0
        for code in missing:
            sleep(0.05)
            try:
                candles = provider.get_kline(code, "1d", KLINE_DEPTH)
            except Exception:  # noqa: BLE001 — one bad ticker must not kill the job
                continue
            _insert_bars(db, code, candles)
            backfilled += 1
        db.commit()

        repaired = _repair_kline_gaps(db, provider)
        deepened = _deepen_kline_history(db, provider)
        logger.info(
            "Reference refresh: industry for %d stocks, fundamentals updated, "
            "klines backfilled for %d new stocks, gaps repaired for %d stocks, "
            "history deepened for %d stocks",
            n_ind, backfilled, repaired, deepened,
        )
    except Exception as exc:  # noqa: BLE001 — keep the scheduler alive
        logger.warning("Reference refresh failed: %s", exc)
    finally:
        db.close()


# 股票池少于这个数视为数据源没给全（限流截断），不据此清理任何东西
_PRUNE_MIN_UNIVERSE = 4000
# 一次要清掉的行情超过现有的这个比例，同样视为股票池不可信
_PRUNE_MAX_SHARE = 0.1
# 北交所股票从行情源消失、且最后一根日 K 早于这么多天，才记为退市
_BJ_DELIST_AFTER = timedelta(days=30)


def _prune_stale(db, live: set[str]) -> tuple[int, int, int]:
    """清掉行情源股票池里已经没有的代码留下的残行。返回 (删行情数, 删北交所旧代码数, 标退市数)。

    行情表只 upsert 不删：退市、改代码之后旧行永远留着，价格冻结在最后一天，
    还会出现在选股结果和「全市场」的计数里（北交所 2025 年统一改 920 代码后，
    244 个旧代码一直留到 2026-11）。这里：
    - 不在股票池里的代码，删掉行情和基本面快照（个股资料与历史 K 线不动）；
    - 北交所没有上市 / 退市日期的数据源（BaoStock 不含北交所）：不在股票池里、且没有任何日 K
      的是改号前的旧代码，连资料行一起删（数据都在新代码下）；有日 K 且已停更超过 30 天的记为退市，
      退市日取最后一根日 K 的次日。
    """
    if len(live) < _PRUNE_MIN_UNIVERSE:
        return 0, 0, 0
    quoted = set(db.execute(select(Quote.code)).scalars())
    stale = quoted - live
    if len(stale) > len(quoted) * _PRUNE_MAX_SHARE:
        logger.warning("Stale cleanup skipped: %d of %d quoted codes missing from the universe",
                       len(stale), len(quoted))
        return 0, 0, 0
    if stale:
        db.execute(delete(Quote).where(Quote.code.in_(stale)))
        db.execute(delete(Fundamental).where(Fundamental.code.in_(stale)))

    gone_bj = [code for code in db.execute(
        select(StockInfo.code).where(StockInfo.market == "BJ", StockInfo.delist_date.is_(None))
    ).scalars() if code not in live]
    renamed = delisted = 0
    if gone_bj:
        last_bar = dict(db.execute(
            select(Kline.code, func.max(Kline.ts))
            .where(Kline.period == "1d", Kline.code.in_(gone_bj)).group_by(Kline.code)
        ).all())
        no_bars = [code for code in gone_bj if code not in last_bar]
        if no_bars:
            db.execute(delete(StockInfo).where(StockInfo.code.in_(no_bars)))
            renamed = len(no_bars)
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - _BJ_DELIST_AFTER
        for code, last in last_bar.items():
            if last < cutoff:
                db.execute(update(StockInfo).where(StockInfo.code == code)
                           .values(delist_date=(last + timedelta(days=1)).date()))
                delisted += 1
    db.commit()
    return len(stale), renamed, delisted


def _repair_kline_gaps(db, provider, window: int = 40, cap: int = 6000) -> int:
    """按数据源的真实交易日历检测并回补日 K 缺口。

    当日合成 bar 只覆盖"今天"——服务停摆几天、或历史上日 K 从未随日更新
    的欠账，会在中间留洞，均线会静默跨洞算错。这里用一只常年不停牌的
    参照股（贵州茅台）拿到真实交易日序列，逐股找出缺失日期再从数据源补。
    长期停牌股会反复被扫到但补不回来（数据源本来就没有），量小、无害。
    """
    try:
        ref = provider.get_kline("600519", "1d", window + 20)
    except Exception:  # noqa: BLE001
        return 0
    truth = {c.ts for c in ref[-window:]}
    if not truth:
        return 0
    window_start = min(truth)

    have: dict[str, set] = {}
    for code, ts in db.execute(
        select(Kline.code, Kline.ts).where(Kline.period == "1d", Kline.ts >= window_start)
    ):
        have.setdefault(code, set()).add(ts)

    repaired = 0
    for code, dates in have.items():
        if repaired >= cap:
            logger.warning("Kline gap repair capped at %d stocks; continuing next run", cap)
            break
        first = min(dates)  # 窗口中段上市的新股，只要求上市之后的日期
        missing = {d for d in truth if d > first and d not in dates}
        if not missing:
            continue
        sleep(0.05)
        try:
            candles = provider.get_kline(code, "1d", window + 20)
        except Exception:  # noqa: BLE001
            continue
        _insert_bars(db, code, [c for c in candles if c.ts in missing])
        repaired += 1
        if repaired % 500 == 0:
            db.commit()
            logger.info("Kline gap repair progress: %d", repaired)
    db.commit()
    return repaired


def _deepen_kline_history(db, provider, cap: int = 3000) -> int:
    """把存量股票的日 K 深度扩到 KLINE_DEPTH（约 3 年），支撑长区间回测。

    早期 bootstrap 只拉了 250 根；这里对深度不足的股票重拉更长历史，
    只插入库里没有的日期。上市不满 3 年的新股每天会被扫到但插不进新行，
    属于少量无害的空转调用。
    """
    counts = {
        code: n
        for code, n in db.execute(
            select(Kline.code, func.count()).where(Kline.period == "1d").group_by(Kline.code)
        )
    }
    deepened = 0
    for code, n in counts.items():
        if n >= KLINE_DEPTH - 60:
            continue
        if deepened >= cap:
            logger.warning("Kline deepen capped at %d stocks; continuing next run", cap)
            break
        sleep(0.05)
        try:
            candles = provider.get_kline(code, "1d", KLINE_DEPTH)
        except Exception:  # noqa: BLE001 — one bad ticker must not kill the job
            continue
        if len(candles) <= n:
            continue  # 新股：历史本来就这么长，不算加深
        have = {
            r[0]
            for r in db.execute(
                select(Kline.ts).where(Kline.code == code, Kline.period == "1d")
            )
        }
        _insert_bars(db, code, [c for c in candles if c.ts not in have])
        deepened += 1
        if deepened % 500 == 0:
            db.commit()
            logger.info("Kline deepen progress: %d", deepened)
    db.commit()
    return deepened


def record_bj_daily_basic() -> None:
    """收盘后把北交所当日的每日指标写入 daily_basic（BaoStock 没有北交所，沪深由数据仓库日更写）。

    取自行情快照，只有收盘后的值才算数：以库里最新日 K 的日期为交易日，盘中和当天 15:00 前（含午休）
    都不写；之后每次都覆盖写（收盘后到次日开盘前快照不变，重跑结果相同）。
    停机漏写的日子无法补（quotes 只有最新一份）。
    """
    db = SessionLocal()
    try:
        if in_trading_session():
            return
        day = db.execute(select(func.max(Kline.ts)).where(Kline.period == "1d")).scalar()
        now_cst = datetime.now(timezone.utc) + timedelta(hours=_CST_OFFSET_HOURS)
        if day is None or (day.date() == now_cst.date() and now_cst.time() < time(15, 0)):
            return
        rows = []
        for q, name in db.execute(
            select(Quote, StockInfo.name)
            .join(StockInfo, StockInfo.code == Quote.code)
            .where(StockInfo.market == "BJ")
        ).tuples():
            if not q.price:
                continue
            rows.append({
                "code": q.code, "trade_date": day.date(), "close": q.price,
                "preclose": q.prev_close or None, "volume": float(q.volume * 100),  # 数据源是手
                "amount": q.turnover * 1e8, "turn": q.turnover_rate,
                "suspended": q.volume <= 0, "is_st": "ST" in (name or "").upper(),
                "pe_ttm": q.pe, "pb_mrq": q.pb, "total_mv": q.market_cap * 1e8 if q.market_cap else None,
            })
        upsert_rows(db, DailyBasic, rows)
        db.commit()
        logger.info("北交所每日指标 %s：%d 只", day.date(), len(rows))
    except Exception as exc:  # noqa: BLE001 — keep the scheduler alive
        logger.warning("北交所每日指标写入失败：%s", exc)
    finally:
        db.close()


def collect_once(force: bool = False) -> None:
    provider = get_provider()
    if not force and provider.name != "seed" and not in_trading_session():
        logger.debug("Outside trading session; skipping collection")
        return
    db = SessionLocal()
    try:
        n = _refresh_quotes(db)
        logger.info("Collected %d quotes", n)
    except Exception as exc:  # noqa: BLE001 — keep the scheduler alive
        logger.warning("Collection failed: %s", exc)
    finally:
        db.close()


def run_bootstrap_and_first_collect() -> None:
    db = SessionLocal()
    try:
        bootstrap(db)
    finally:
        db.close()
    # Force the first collection even off-hours so a fresh deploy serves the
    # latest close instead of empty dashboards until the next trading session.
    collect_once(force=True)
