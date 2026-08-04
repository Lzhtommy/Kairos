"""盘后策略自动运行：15:25 CST 跑全部已保存策略，diff 命中并推送变化。

幂等设计：run_date = 库里最新日 K 的交易日；同一策略同一 run_date 只落
一条记录，重复调度/重启补跑都不会重复通知。停机漏跑的历史日子不补
（quotes/因子已是新值，补出来的也不是当时的命中），下一个交易日自然续上。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from statistics import mean

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models.market import Kline, Quote
from app.models.strategy import Strategy, StrategyRun
from app.models.user import User, UserSetting
from app.services import notify
from app.services.strategy_exec import run_dsl

logger = logging.getLogger("kairos.strategy_runs")

_LIST_CAP = 50  # added/removed 明细留存上限（计数保留全量）


def _now_cst() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=8)


def run_all_strategies(force: bool = False) -> int:
    """返回本次新建的 run 数。force 跳过时间闸门（测试/手动触发用）。"""
    db = SessionLocal()
    try:
        latest = db.execute(
            select(func.max(Kline.ts)).where(Kline.period == "1d")
        ).scalar()
        if latest is None:
            return 0
        now_cst = _now_cst()
        if not force:
            # 只在"当日收盘后"运行：最新日 K 必须是今天（非交易日是上个交易日，
            # 直接跳过），且已过 15:10（盘中当日 bar 还在滚动，不是收盘口径）
            if latest.date() != now_cst.date() or now_cst.time().hour * 60 + now_cst.time().minute < 15 * 60 + 10:
                return 0
        run_date = latest

        # dsl 为空的在循环里跳过（JSON 列的 != {} 在 SQLite 上不可靠）
        strategies = db.execute(select(Strategy)).scalars().all()
        done_ids = {
            r[0]
            for r in db.execute(
                select(StrategyRun.strategy_id).where(StrategyRun.run_date == run_date)
            )
        }

        new_runs: list[StrategyRun] = []
        names_of: dict[int, dict[str, str]] = {}
        for s in strategies:
            if s.id in done_ids or not s.dsl:
                continue
            try:
                rows = run_dsl(db, dict(s.dsl))
            except Exception:  # noqa: BLE001 — 单个策略坏 DSL 不能拦住全场
                logger.exception("strategy %s run failed", s.id)
                continue
            codes = sorted(r["code"] for r in rows)
            names = {r["code"]: r["name"] for r in rows}
            prev = db.execute(
                select(StrategyRun)
                .where(StrategyRun.strategy_id == s.id, StrategyRun.run_date < run_date)
                .order_by(StrategyRun.run_date.desc())
                .limit(1)
            ).scalar()
            if prev is not None:
                prev_set = set(prev.hit_codes or [])
                added = [c for c in codes if c not in prev_set]
                removed = sorted(prev_set - set(codes))
                prev_names = {x["code"]: x["name"] for x in (prev.added or [])}
            else:
                added, removed, prev_names = [], [], {}  # 首次运行是基线，不算"新进"
            run = StrategyRun(
                strategy_id=s.id,
                run_date=run_date,
                hit_codes=codes,
                added=[{"code": c, "name": names.get(c, c)} for c in added[:_LIST_CAP]],
                removed=[
                    {"code": c, "name": prev_names.get(c, c)} for c in removed[:_LIST_CAP]
                ],
                added_count=len(added),
                removed_count=len(removed),
                hit_count=len(codes),
            )
            db.add(run)
            s.hit_count = len(codes)
            new_runs.append(run)
            names_of[s.id] = names
        db.commit()

        if new_runs:
            _notify_users(db, new_runs, run_date)
        filled = _backfill_forward(db, run_date)
        from app.services.paper import settle_all

        settled = settle_all(db)  # 模拟盘逐日结算（含停机补结算），依赖当日 run 的信号
        logger.info(
            "strategy runs: %d new for %s, forward filled %d, paper settled %d",
            len(new_runs), run_date.date(), filled, settled,
        )
        return len(new_runs)
    finally:
        db.close()


# ---- 信号前瞻跟踪（模拟盘第一步：样本外验证信号本身） ---------------------------

_HORIZONS = (1, 5, 10)
_TRACK_DAYS = 45  # 只回填最近 ~45 自然日（覆盖 D+10 成熟窗口），更早的不再动
_CODES_CAP = 800  # 单次 IN 查询上限（SQLite 参数数约束），超出截断并计入日志


def _forward_for_run(db, run: StrategyRun) -> dict | None:
    """一条 run 的前瞻收益：信号次日开盘入场，D+N 收盘相对入场价，等权平均。

    只结算已成熟的 horizon（后市交易日数足够）；停牌缺 bar 的股票不计入该档。
    返回增量更新后的 forward，无新成熟档位时返回 None。
    """
    codes = list(run.hit_codes or [])
    if not codes:
        return {f"d{h}": {"n": 0, "avg": 0.0, "win": 0.0} for h in _HORIZONS}
    if len(codes) > _CODES_CAP:
        logger.warning("run %s hit %d codes, forward tracking caps at %d",
                       run.id, len(codes), _CODES_CAP)
        codes = codes[:_CODES_CAP]

    need = max(_HORIZONS)
    series: dict[str, list[tuple[float, float]]] = {}  # code -> [(open, close)] 升序
    for code, o, c in db.execute(
        select(Kline.code, Kline.open, Kline.close)
        .where(Kline.period == "1d", Kline.code.in_(codes), Kline.ts > run.run_date)
        .order_by(Kline.code, Kline.ts.asc())
    ):
        s = series.setdefault(code, [])
        if len(s) < need:
            s.append((o, c))
    if not series:
        return None  # 尚无后市数据

    # 成熟度按"命中股中后市 bar 最多者"判定：个别停牌不至于拖住整条 run
    elapsed = max(len(s) for s in series.values())
    out = dict(run.forward or {})
    changed = False
    for h in _HORIZONS:
        key = f"d{h}"
        if key in out or elapsed < h:
            continue
        rets = []
        for s in series.values():
            if len(s) < h or not s[0][0]:
                continue  # 停牌缺 bar / 无开盘价
            rets.append(s[h - 1][1] / s[0][0] - 1)
        out[key] = {
            "n": len(rets),
            "avg": round(mean(rets) * 100, 2) if rets else 0.0,
            "win": round(100 * sum(r > 0 for r in rets) / len(rets), 1) if rets else 0.0,
        }
        changed = True
    return out if changed else None


def _backfill_forward(db, latest: datetime) -> int:
    """给窗口内 forward 未完结（缺 d10）的 run 补记已成熟的档位。幂等增量。"""
    cutoff = latest - timedelta(days=_TRACK_DAYS)
    runs = db.execute(
        select(StrategyRun).where(StrategyRun.run_date >= cutoff)
    ).scalars().all()
    filled = 0
    for r in runs:
        if f"d{max(_HORIZONS)}" in (r.forward or {}):
            continue
        fwd = _forward_for_run(db, r)
        if fwd is not None:
            r.forward = fwd
            filled += 1
    if filled:
        db.commit()
    return filled


def _notify_users(db, runs: list[StrategyRun], run_date: datetime) -> None:
    """按用户聚合成一条通知；只有存在新进/调出的策略才提及，全无变化不打扰。"""
    strategies = {
        s.id: s
        for s in db.execute(
            select(Strategy).where(Strategy.id.in_([r.strategy_id for r in runs]))
        ).scalars()
    }
    by_user: dict[int, list[str]] = {}
    for r in runs:
        s = strategies.get(r.strategy_id)
        added_n, removed_n = r.added_count, r.removed_count
        if s is None or (added_n == 0 and removed_n == 0):
            continue
        sample = "、".join(f"{x['name']}({x['code']})" for x in (r.added or [])[:5])
        line = f"「{s.name}」命中 {r.hit_count} 只：新进 {added_n}，调出 {removed_n}"
        if sample:
            line += f"｜新进示例：{sample}"
        by_user.setdefault(s.user_id, []).append(line)

    if not by_user:
        return
    settings_map = {
        us.user_id: us
        for us in db.execute(
            select(UserSetting).where(UserSetting.user_id.in_(by_user))
        ).scalars()
    }
    users = {
        u.id: u
        for u in db.execute(select(User).where(User.id.in_(by_user))).scalars()
    }
    day = run_date.strftime("%Y-%m-%d")
    for uid, lines in by_user.items():
        us = settings_map.get(uid)
        if us is None or us.notify_channel == "none" or not us.notify_target:
            continue
        title = f"Kairos 策略日报 {day}"
        text = "\n".join(lines) + "\n\n来自 Kairos 盘后自动运行"
        ok = notify.send(us.notify_channel, us.notify_target, title, text)
        logger.info("notify user %s (%s): %s", uid, users.get(uid, uid), "ok" if ok else "failed")


# ---- 数据新鲜度告警（P5.2）------------------------------------------------------


def check_data_freshness() -> None:
    """盘中 quotes 滞后 >30 分钟 → 管理员告警。半小时一次由调度器驱动。"""
    from app.core.config import settings as cfg
    from app.jobs.collect import in_trading_session

    if cfg.alert_channel == "none" or not cfg.alert_target:
        return
    if not in_trading_session():
        return
    db = SessionLocal()
    try:
        ts = db.execute(select(func.max(Quote.ts))).scalar()
        lag = None
        if ts is not None:
            aware = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            lag = (datetime.now(timezone.utc) - aware).total_seconds()
        if lag is None or lag > 1800:
            mins = "∞" if lag is None else f"{int(lag // 60)}"
            notify.send(
                cfg.alert_channel,
                cfg.alert_target,
                "Kairos 数据断供告警",
                f"交易时段内行情快照已 {mins} 分钟未更新，请检查采集器/数据源。",
            )
    finally:
        db.close()
