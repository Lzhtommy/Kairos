import { useEffect, useMemo, useRef, useState } from "react"
import type { ValuationHistory, ValuationPoint } from "@/api/sector"
import { cn } from "@/lib/utils"

const H = 280
const M = { l: 44, r: 12, t: 12, b: 24 }

// 与表格估值标签同色：低位=天蓝、高位=琥珀
const LOW_COLOR = "#0ea5e9"
const HIGH_COLOR = "#f59e0b"

type Metric = "pe" | "pb"
type Range = "3y" | "5y" | "all"

const RANGES: { key: Range; label: string; years: number | null }[] = [
  { key: "3y", label: "3 年", years: 3 },
  { key: "5y", label: "5 年", years: 5 },
  { key: "all", label: "全部", years: null },
]

function useContainerWidth() {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver((entries) => setWidth(entries[0].contentRect.width))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return { ref, width }
}

function niceStep(range: number, target = 4): number {
  const raw = range / target
  const mag = 10 ** Math.floor(Math.log10(raw || 1))
  for (const m of [1, 2, 2.5, 5, 10]) {
    if (raw <= m * mag) return m * mag
  }
  return 10 * mag
}

/** 连续非空区间 [start, end]，用于缺失处断线 / 断带。 */
function runs(n: number, ok: (i: number) => boolean): [number, number][] {
  const out: [number, number][] = []
  let s = -1
  for (let i = 0; i <= n; i++) {
    if (i < n && ok(i)) {
      if (s < 0) s = i
    } else if (s >= 0) {
      out.push([s, i - 1])
      s = -1
    }
  }
  return out
}

function pick(p: ValuationPoint, m: Metric) {
  return { v: p[m], lo: p[`${m}_lo`], hi: p[`${m}_hi`], pct: p[`${m}_pct`] }
}

function zoneLabel(pct: number | null): string | null {
  if (pct == null) return null
  return pct < 0.2 ? "低位" : pct > 0.8 ? "高位" : "中位"
}

function Segmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T
  options: { key: T; label: string }[]
  onChange: (v: T) => void
}) {
  return (
    <div className="inline-flex rounded-md border border-border p-0.5">
      {options.map((o) => (
        <button
          key={o.key}
          type="button"
          onClick={() => onChange(o.key)}
          className={cn(
            "rounded px-2 py-0.5 text-xs transition-colors",
            value === o.key ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

/**
 * 行业估值趋势：主线为整体法 PE / PB 原值，背景按滚动 5 年分位的 20% / 80%
 * 边界着色为低位 / 中位 / 高位区。边界随窗口滚动，和表格分位标签同一口径。
 */
export function ValuationChart({ history }: { history: ValuationHistory }) {
  const { ref, width } = useContainerWidth()
  const [metric, setMetric] = useState<Metric>(history.primary)
  const [range, setRange] = useState<Range>("5y")
  const [hover, setHover] = useState<number | null>(null)

  const pts = useMemo(() => {
    const years = RANGES.find((r) => r.key === range)!.years
    if (years == null || !history.points.length) return history.points
    const last = new Date(history.points[history.points.length - 1].d)
    last.setFullYear(last.getFullYear() - years)
    const cutoff = last.toISOString().slice(0, 10)
    return history.points.filter((p) => p.d >= cutoff)
  }, [history, range])

  const get = (p: ValuationPoint) => pick(p, metric)

  const model = useMemo(() => {
    const n = pts.length
    if (n < 2 || width < M.l + M.r + 40) return null
    const vals: number[] = []
    for (const p of pts) {
      const g = pick(p, metric)
      for (const x of [g.v, g.lo, g.hi]) if (x != null) vals.push(x)
    }
    if (!vals.length) return null
    let lo = Math.min(...vals)
    let hi = Math.max(...vals)
    const pad = (hi - lo || hi * 0.1 || 1) * 0.06
    lo -= pad
    hi += pad
    const iw = width - M.l - M.r
    const ih = H - M.t - M.b
    const x = (i: number) => M.l + (i / (n - 1)) * iw
    const y = (v: number) => M.t + (1 - (v - lo) / (hi - lo)) * ih

    const line = (idx: [number, number][], val: (i: number) => number) =>
      idx
        .map(([s, e]) => {
          let d = ""
          for (let i = s; i <= e; i++) d += `${i === s ? "M" : "L"}${x(i).toFixed(1)},${y(val(i)).toFixed(1)}`
          return d
        })
        .join("")

    const bandRuns = runs(n, (i) => pick(pts[i], metric).lo != null)
    const valueRuns = runs(n, (i) => pick(pts[i], metric).v != null)
    const top = M.t
    const bottom = H - M.b
    // 区域：高位区 = 80% 线到顶部；低位区 = 20% 线到底部
    const zone = (key: "lo" | "hi", edge: number) =>
      bandRuns
        .map(([s, e]) => {
          let d = ""
          for (let i = s; i <= e; i++) d += `${i === s ? "M" : "L"}${x(i).toFixed(1)},${y(pick(pts[i], metric)[key]!).toFixed(1)}`
          return `${d}L${x(e).toFixed(1)},${edge}L${x(s).toFixed(1)},${edge}Z`
        })
        .join("")

    const step = niceStep(hi - lo)
    const yTicks: number[] = []
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) yTicks.push(v)
    const xCount = Math.min(6, n)
    const xTicks = Array.from({ length: xCount }, (_, k) => Math.round((k / (xCount - 1)) * (n - 1)))

    return {
      x, y, iw, yTicks, xTicks, step,
      valuePath: line(valueRuns, (i) => pick(pts[i], metric).v!),
      loPath: line(bandRuns, (i) => pick(pts[i], metric).lo!),
      hiPath: line(bandRuns, (i) => pick(pts[i], metric).hi!),
      lowZone: zone("lo", bottom),
      highZone: zone("hi", top),
    }
  }, [pts, metric, width])

  const latest = history.points.length ? get(history.points[history.points.length - 1]) : null
  const hp = hover != null ? pts[hover] : null
  const hg = hp ? get(hp) : null
  const metricName = metric === "pe" ? "市盈率" : "市净率"
  const digits = metric === "pe" ? 2 : 3
  const fmtTick = (v: number) => (model && model.step < 1 ? v.toFixed(metric === "pb" ? 2 : 1) : String(Math.round(v)))
  const shortWindow = history.window_days < history.full_window_days

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h3 className="text-sm font-medium">{history.name} · 估值趋势</h3>
          {latest?.v != null && (
            <span className="font-mono text-xs tabular-nums text-muted-foreground">
              {metric.toUpperCase()} {latest.v.toFixed(digits)}
              {latest.pct != null && ` · 分位 ${Math.round(latest.pct * 100)}% · ${zoneLabel(latest.pct)}`}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Segmented
            value={metric}
            onChange={setMetric}
            options={[
              { key: "pe", label: history.primary === "pe" ? "PE（主）" : "PE" },
              { key: "pb", label: history.primary === "pb" ? "PB（主）" : "PB" },
            ]}
          />
          <Segmented value={range} onChange={setRange} options={RANGES} />
        </div>
      </div>

      <div ref={ref} className="relative w-full select-none">
        {model && (
          <svg
            width={width}
            height={H}
            onMouseMove={(e) => {
              const rect = e.currentTarget.getBoundingClientRect()
              const idx = Math.round(((e.clientX - rect.left - M.l) / model.iw) * (pts.length - 1))
              setHover(Math.min(pts.length - 1, Math.max(0, idx)))
            }}
            onMouseLeave={() => setHover(null)}
          >
            <path d={model.highZone} fill={HIGH_COLOR} fillOpacity="0.12" />
            <path d={model.lowZone} fill={LOW_COLOR} fillOpacity="0.12" />
            {model.yTicks.map((v) => (
              <g key={v}>
                <line
                  x1={M.l} x2={width - M.r} y1={model.y(v)} y2={model.y(v)}
                  stroke="var(--color-border, #e5e7eb)" strokeWidth="1" opacity="0.45"
                />
                <text
                  x={M.l - 6} y={model.y(v) + 3} textAnchor="end"
                  className="fill-muted-foreground font-mono" fontSize="10"
                >
                  {fmtTick(v)}
                </text>
              </g>
            ))}
            {model.xTicks.map((i, k) => (
              <text
                key={i}
                x={model.x(i)} y={H - 6}
                textAnchor={k === 0 ? "start" : k === model.xTicks.length - 1 ? "end" : "middle"}
                className="fill-muted-foreground font-mono" fontSize="10"
              >
                {pts[i].d.slice(2, 7)}
              </text>
            ))}
            <path d={model.hiPath} fill="none" stroke={HIGH_COLOR} strokeWidth="1" strokeDasharray="4 3" opacity="0.8" />
            <path d={model.loPath} fill="none" stroke={LOW_COLOR} strokeWidth="1" strokeDasharray="4 3" opacity="0.8" />
            <path d={model.valuePath} fill="none" className="stroke-foreground" strokeWidth="1.5" />
            {hp && (
              <g>
                <line
                  x1={model.x(hover!)} x2={model.x(hover!)} y1={M.t} y2={H - M.b}
                  stroke="var(--color-muted-foreground, #9ca3af)" strokeWidth="1" strokeDasharray="2 2" opacity="0.7"
                />
                {hg?.v != null && (
                  <circle cx={model.x(hover!)} cy={model.y(hg.v)} r="3" className="fill-foreground" />
                )}
              </g>
            )}
          </svg>
        )}
        {hp && hg && model && (
          <div
            className="pointer-events-none absolute top-2 rounded-md border border-border bg-popover px-2.5 py-1.5 text-xs shadow-md"
            style={{ left: Math.min(Math.max(model.x(hover!) + 10, M.l), width - 170) }}
          >
            <p className="font-mono tabular-nums text-muted-foreground">{hp.d}</p>
            <p className="font-mono tabular-nums">
              {metricName} {hg.v == null ? "亏损 / 缺失" : hg.v.toFixed(digits)}
              {hg.pct != null && (
                <span className="text-muted-foreground">
                  {" "}· {Math.round(hg.pct * 100)}% {zoneLabel(hg.pct)}
                </span>
              )}
            </p>
            {hg.lo != null && hg.hi != null && (
              <p className="font-mono tabular-nums text-muted-foreground">
                中位区 {hg.lo.toFixed(digits)} ~ {hg.hi.toFixed(digits)}
              </p>
            )}
            {hp.dp != null && <p className="font-mono tabular-nums text-muted-foreground">股息率 {hp.dp.toFixed(2)}%</p>}
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-1 text-[11px] text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-4 rounded bg-foreground" />
          {metricName}（整体法）
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block size-2.5 rounded-sm" style={{ background: HIGH_COLOR, opacity: 0.35 }} />
          高位区（&gt; 80% 分位）
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block size-2.5 rounded-sm border border-border" />
          中位区
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block size-2.5 rounded-sm" style={{ background: LOW_COLOR, opacity: 0.35 }} />
          低位区（&lt; 20% 分位）
        </span>
      </div>
      <p className="px-1 text-[11px] leading-relaxed text-muted-foreground">
        区域边界为滚动近 {shortWindow ? `约 ${(history.window_days / 250).toFixed(1)}` : 5} 年分位，只描述相对自身历史的位置；
        长期处于低位可能是盈利中枢下移而非便宜。前 3 年历史不足不出分位，行业整体亏损时断线。
        {shortWindow && (
          <span className="text-amber-600 dark:text-amber-400">
            {" "}该行业历史仅 {history.window_days} 个交易日，分位窗口不足 5 年。
          </span>
        )}
      </p>
    </div>
  )
}
