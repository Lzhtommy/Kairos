"""行业轮动指标计算（research/sector_rotation 公式的纯 Python 移植）。

刻意不用 pandas：核心镜像/CI 不带数据依赖，面板规模（≤90 板块 × 数千日）
纯 Python 完全扛得住。公式与研究脚本一一对应，改动前先对研究结论：

- 拥挤度（phase3_crowding.build_crowding）：成交额占比 20 日均 / 换手热度
  （20 日均÷250 日均）/ 60 日乖离率，各取自身 250 日滚动分位后平均。
  研究结论：无预测力（Step 1 未过 gate），只作过热预警展示，不参与倾斜。
- RRG（make_report.calc_rrg）：周频 RS-Ratio = 100 + 相对强度 26 周 z 分数，
  RS-Momentum = 100 + RS-Ratio 的 4 周变化。
- 倾斜（phase2/phase3_resmom 三因子）：rev1（上月残差收益）、season（历史
  同月均值，≥5 年）、resmom12（36 月滚动 beta 残差 12-1 累计，波动率缩放）。
  合成不用 Ridge（产品内不训练），改为 trailing Fama-MacBeth IC 定因子符号
  后等权加 z 分数——rev1 在申万一级面板是延续、在二级面板是反转，符号
  必须由面板自身历史决定，不能写死。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

MIN_XS = 10          # 截面最少板块数（与研究一致）
IC_MIN_MONTHS = 24   # trailing IC 判定因子符号所需的最少月份数
TILT_TOP_FRAC = 0.2  # 高配/低配各取截面前后 20%


# ---------------------------------------------------------------- 面板
@dataclass
class Panel:
    """日期对齐的板块面板。dates 升序；closes/amounts 与 dates 等长，缺数据为 None。"""

    dates: list[datetime]
    closes: dict[str, list[float | None]]
    amounts: dict[str, list[float | None]] = field(default_factory=dict)

    @property
    def codes(self) -> list[str]:
        return list(self.closes)


def build_panel(bars_by_code: dict[str, list[tuple[datetime, float, float]]]) -> Panel:
    """bars_by_code: code -> [(ts, close, amount)]，无需预先对齐。"""
    all_dates = sorted({ts for bars in bars_by_code.values() for ts, _, _ in bars})
    idx = {d: i for i, d in enumerate(all_dates)}
    closes: dict[str, list[float | None]] = {}
    amounts: dict[str, list[float | None]] = {}
    for code, bars in bars_by_code.items():
        c: list[float | None] = [None] * len(all_dates)
        a: list[float | None] = [None] * len(all_dates)
        for ts, close, amount in bars:
            i = idx[ts]
            c[i] = close
            a[i] = amount
        closes[code] = c
        amounts[code] = a
    return Panel(dates=all_dates, closes=closes, amounts=amounts)


# ---------------------------------------------------------------- 基础算子
def _rolling_mean(xs: list[float | None], window: int, min_periods: int) -> list[float | None]:
    """位置窗口滚动均值（含当前位置），非 None 值 ≥ min_periods 才出值。"""
    out: list[float | None] = [None] * len(xs)
    s, n = 0.0, 0
    for i, x in enumerate(xs):
        if x is not None:
            s += x
            n += 1
        if i >= window:
            old = xs[i - window]
            if old is not None:
                s -= old
                n -= 1
        if n >= min_periods:
            out[i] = s / n
    return out


def _std(vals: list[float]) -> float | None:
    if len(vals) < 2:
        return None
    m = sum(vals) / len(vals)
    var = sum((v - m) ** 2 for v in vals) / (len(vals) - 1)
    return math.sqrt(var)


def _rank_pct(window_vals: list[float], current: float) -> float:
    """current 在窗口（含自身）中的平均名次百分位，对齐 pandas rolling.rank(pct=True)。"""
    less = sum(1 for v in window_vals if v < current)
    equal = sum(1 for v in window_vals if v == current)
    return (less + (equal + 1) / 2) / len(window_vals)


def _spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None

    def ranks(xs: list[float]) -> list[float]:
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        r = [0.0] * len(xs)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = math.sqrt(sum((x - ma) ** 2 for x in ra))
    vb = math.sqrt(sum((y - mb) ** 2 for y in rb))
    if va == 0 or vb == 0:
        return None
    return cov / (va * vb)


def _xs_z(values: dict[str, float | None]) -> dict[str, float | None]:
    ok = [v for v in values.values() if v is not None]
    if len(ok) < 2:
        return {c: None for c in values}
    m = sum(ok) / len(ok)
    s = _std(ok)
    if not s:
        return {c: None for c in values}
    return {c: (None if v is None else (v - m) / s) for c, v in values.items()}


# ---------------------------------------------------------------- 拥挤度
def crowding_rows(panel: Panel, since: datetime | None = None) -> list[dict]:
    """逐板块逐日拥挤度。since 之前的日期跳过（增量更新只算缺的部分）。

    返回 [{code, ts, crowd, share, heat, bias}]，分位值可能为 None（历史不足）。
    """
    n = len(panel.dates)
    totals: list[float | None] = []
    for i in range(n):
        vals = [panel.amounts[c][i] for c in panel.codes if panel.amounts.get(c)]
        vals = [v for v in vals if v is not None]
        totals.append(sum(vals) if vals else None)

    out: list[dict] = []
    for code in panel.codes:
        close = panel.closes[code]
        amount = panel.amounts.get(code) or [None] * n
        share_raw = [
            (amount[i] / totals[i]) if (amount[i] is not None and totals[i]) else None
            for i in range(n)
        ]
        share = _rolling_mean(share_raw, 20, 15)
        amt20 = _rolling_mean(amount, 20, 15)
        amt250 = _rolling_mean(amount, 250, 200)
        heat = [
            (amt20[i] / amt250[i]) if (amt20[i] is not None and amt250[i]) else None
            for i in range(n)
        ]
        ma60 = _rolling_mean(close, 60, 45)
        bias = [
            (close[i] / ma60[i] - 1) if (close[i] is not None and ma60[i]) else None
            for i in range(n)
        ]

        for i in range(n):
            if close[i] is None:
                continue
            if since is not None and panel.dates[i] < since:
                continue
            pcts: list[float | None] = []
            for sub in (share, heat, bias):
                if sub[i] is None:
                    pcts.append(None)
                    continue
                window = [v for v in sub[max(0, i - 249): i + 1] if v is not None]
                pcts.append(_rank_pct(window, sub[i]) if len(window) >= 200 else None)
            ok = [p for p in pcts if p is not None]
            out.append(
                {
                    "code": code,
                    "ts": panel.dates[i],
                    "crowd": (sum(ok) / len(ok)) if ok else None,
                    "share": pcts[0],
                    "heat": pcts[1],
                    "bias": pcts[2],
                }
            )
    return out


# ---------------------------------------------------------------- RRG
def _daily_returns(panel: Panel) -> dict[str, list[float | None]]:
    rets: dict[str, list[float | None]] = {}
    for code, close in panel.closes.items():
        r: list[float | None] = [None] * len(close)
        for i in range(1, len(close)):
            if close[i] is not None and close[i - 1]:
                r[i] = close[i] / close[i - 1] - 1
        rets[code] = r
    return rets


def _week_end(d: datetime) -> datetime:
    """所属周的周五（W-FRI 口径；周六/日归下一个周五）。"""
    wd = d.weekday()
    delta = 4 - wd if wd <= 4 else 11 - wd
    return d + timedelta(days=delta)


def calc_rrg(panel: Panel, trail_weeks: int = 6) -> dict:
    """周频 JdK 风格 RRG（与 make_report.calc_rrg 同式）。"""
    n = len(panel.dates)
    rets = _daily_returns(panel)
    bench_nav: list[float | None] = [None] * n
    nav = 1.0
    for i in range(n):
        day = [rets[c][i] for c in panel.codes if rets[c][i] is not None]
        if day:
            nav *= 1 + sum(day) / len(day)
        bench_nav[i] = nav

    # 周末取样：每周最后一个交易日
    week_last: dict[datetime, int] = {}
    for i, d in enumerate(panel.dates):
        week_last[_week_end(d)] = i
    weeks = sorted(week_last)
    if not weeks:
        return {"asof": None, "sectors": []}
    w_bench = [bench_nav[week_last[w]] for w in weeks]

    out: dict = {"asof": panel.dates[week_last[weeks[-1]]].date().isoformat(), "sectors": []}
    for code in panel.codes:
        close = panel.closes[code]
        w_close = [close[week_last[w]] for w in weeks]
        rs: list[float | None] = [
            (c / b) if (c is not None and b) else None for c, b in zip(w_close, w_bench)
        ]
        rsr: list[float | None] = [None] * len(rs)
        for i in range(len(rs)):
            if rs[i] is None:
                continue
            window = [v for v in rs[max(0, i - 25): i + 1] if v is not None]
            if len(window) < 26:
                continue
            s = _std(window)
            if not s:
                continue
            rsr[i] = 100 + (rs[i] - sum(window) / len(window)) / s
        rsm: list[float | None] = [None] * len(rs)
        for i in range(4, len(rs)):
            if rsr[i] is not None and rsr[i - 4] is not None:
                rsm[i] = 100 + (rsr[i] - rsr[i - 4])

        tail_r = rsr[-trail_weeks:]
        tail_m = rsm[-trail_weeks:]
        if len(tail_r) < trail_weeks or any(v is None for v in tail_r + tail_m):
            continue
        rel4w = None
        if len(weeks) >= 5 and w_close[-1] is not None and w_close[-5]:
            rel4w = round((w_close[-1] / w_close[-5] - w_bench[-1] / w_bench[-5]) * 100, 2)
        out["sectors"].append(
            {
                "code": code,
                "trail": [[round(x, 2), round(y, 2)] for x, y in zip(tail_r, tail_m)],
                "rel4w": rel4w,
            }
        )
    return out


# ---------------------------------------------------------------- 月频倾斜
def month_key(d: datetime) -> datetime:
    return datetime(d.year, d.month, 1)


def monthly_panel(panel: Panel, min_days: int = 10) -> tuple[list[datetime], dict[str, list[float | None]]]:
    """月度收益（日收益 log 求和再还原，当月 ≥ min_days 个日收益才出值）。"""
    rets = _daily_returns(panel)
    months: list[datetime] = []
    pos: dict[datetime, list[int]] = {}
    for i, d in enumerate(panel.dates):
        mk = month_key(d)
        if mk not in pos:
            pos[mk] = []
            months.append(mk)
        pos[mk].append(i)
    mret: dict[str, list[float | None]] = {}
    for code in panel.codes:
        row: list[float | None] = []
        for mk in months:
            vals = [rets[code][i] for i in pos[mk] if rets[code][i] is not None]
            row.append(math.expm1(sum(math.log1p(v) for v in vals)) if len(vals) >= min_days else None)
        mret[code] = row
    return months, mret


def _demean(mret: dict[str, list[float | None]], n_months: int) -> dict[str, list[float | None]]:
    dm: dict[str, list[float | None]] = {c: [None] * n_months for c in mret}
    for mi in range(n_months):
        vals = [(c, mret[c][mi]) for c in mret if mret[c][mi] is not None]
        if len(vals) < MIN_XS:
            continue
        mean = sum(v for _, v in vals) / len(vals)
        for c, v in vals:
            dm[c][mi] = v - mean
    return dm


def _season(dm: dict[str, list[float | None]], months: list[datetime]) -> dict[str, list[float | None]]:
    """t 时刻的特征 = 「下个日历月」的历史同月 dm 均值（严格过去，≥5 个）。"""
    out: dict[str, list[float | None]] = {c: [None] * len(months) for c in dm}
    for c, row in dm.items():
        # 按日历月累积历史 dm 值，查询 mi 时桶里只有 mi 及之前的值
        buckets: dict[int, list[float]] = {m: [] for m in range(1, 13)}
        for mi in range(len(months)):
            if row[mi] is not None:
                buckets[months[mi].month].append(row[mi])
            target = months[mi].month % 12 + 1
            past = buckets[target]
            if len(past) >= 5:
                out[c][mi] = sum(past) / len(past)
    return out


def _resmom12(mret: dict[str, list[float | None]], n_months: int,
              beta_win: int = 36, min_beta: int = 24) -> dict[str, list[float | None]]:
    """36 月滚动 beta 残差 → 12-1 累计 / 波动率缩放（phase3_resmom 同式）。"""
    mkt: list[float | None] = []
    for mi in range(n_months):
        vals = [mret[c][mi] for c in mret if mret[c][mi] is not None]
        mkt.append(sum(vals) / len(vals) if len(vals) >= MIN_XS else None)

    out: dict[str, list[float | None]] = {}
    for c, row in mret.items():
        resid: list[float | None] = [None] * n_months
        for mi in range(n_months):
            if row[mi] is None or mkt[mi] is None:
                continue
            lo = max(0, mi - beta_win + 1)
            pairs = [(row[j], mkt[j]) for j in range(lo, mi + 1)
                     if row[j] is not None and mkt[j] is not None]
            if len(pairs) < min_beta:
                continue
            mr = sum(p[0] for p in pairs) / len(pairs)
            mm = sum(p[1] for p in pairs) / len(pairs)
            var = sum((p[1] - mm) ** 2 for p in pairs) / len(pairs)
            if var == 0:
                continue
            cov = sum((p[0] - mr) * (p[1] - mm) for p in pairs) / len(pairs)
            beta = cov / var
            resid[mi] = row[mi] - beta * mkt[mi]
        # 去 alpha：残差再减自身 36 月滚动均值
        alpha = _rolling_mean(resid, beta_win, min_beta)
        resid = [
            (resid[i] - alpha[i]) if (resid[i] is not None and alpha[i] is not None) else None
            for i in range(n_months)
        ]
        rm: list[float | None] = [None] * n_months
        for mi in range(11, n_months):
            window = resid[mi - 11: mi]  # t-11 .. t-1，跳过当月
            vals = [v for v in window if v is not None]
            if len(vals) < 11:
                continue
            s = _std(vals)
            if not s:
                continue
            rm[mi] = sum(vals) / s
        out[c] = rm
    return out


def _factor_ics(feat: dict[str, list[float | None]], dm: dict[str, list[float | None]],
                n_months: int) -> list[tuple[int, float]]:
    """逐月 Fama-MacBeth IC 序列 [(月索引, IC)]（特征 mi 对 fwd = dm[mi+1]）。"""
    out: list[tuple[int, float]] = []
    for mi in range(n_months - 1):
        a, b = [], []
        for c in feat:
            f, fwd = feat[c][mi], dm[c][mi + 1]
            if f is not None and fwd is not None:
                a.append(f)
                b.append(fwd)
        if len(a) < MIN_XS:
            continue
        ic = _spearman(a, b)
        if ic is not None:
            out.append((mi, ic))
    return out


def tilt_rows(panel: Panel, only_months: set[datetime] | None = None) -> list[dict]:
    """逐完整月份的倾斜建议（月 T 的行 = 基于 T 月末数据、作用于 T+1 月）。

    最后一个（可能不完整的）月不出建议。返回
    [{code, month, rev1_z, season_z, resmom_z, score, rank, suggestion}]。
    """
    months, mret = monthly_panel(panel)
    if len(months) < 2:
        return []
    n_months = len(months)
    dm = _demean(mret, n_months)
    feats = {
        "rev1": dm,
        "season": _season(dm, months),
        "resmom": _resmom12(mret, n_months),
    }
    ic_series = {f: _factor_ics(feats[f], dm, n_months) for f in feats}

    def sign_at(f: str, mi: int) -> int:
        # 只用 mi 之前的 IC（无前视）；样本不足时用研究默认符号 +1
        past = [ic for mj, ic in ic_series[f] if mj < mi]
        if len(past) < IC_MIN_MONTHS:
            return 1
        return 1 if sum(past) >= 0 else -1

    out: list[dict] = []
    for mi in range(n_months - 1):  # 最后一个月视为不完整，跳过
        mk = months[mi]
        if only_months is not None and mk not in only_months:
            continue
        zs = {f: _xs_z({c: feats[f][c][mi] for c in panel.codes}) for f in feats}
        signs = {f: sign_at(f, mi) for f in feats}
        scores: dict[str, float | None] = {}
        for c in panel.codes:
            parts = [signs[f] * zs[f][c] for f in feats if zs[f][c] is not None]
            scores[c] = (sum(parts) / len(parts)) if parts else None
        ranked = sorted(
            (c for c in panel.codes if scores[c] is not None),
            key=lambda c: scores[c], reverse=True,  # type: ignore[arg-type]
        )
        if len(ranked) < MIN_XS:
            continue
        k = max(1, round(len(ranked) * TILT_TOP_FRAC))
        for c in panel.codes:
            if scores[c] is None:
                continue
            r = ranked.index(c) + 1
            suggestion = "高配" if r <= k else ("低配" if r > len(ranked) - k else "标配")
            out.append(
                {
                    "code": c,
                    "month": mk,
                    "rev1_z": zs["rev1"][c],
                    "season_z": zs["season"][c],
                    "resmom_z": zs["resmom"][c],
                    "score": scores[c],
                    "rank": r,
                    "suggestion": suggestion,
                }
            )
    return out
