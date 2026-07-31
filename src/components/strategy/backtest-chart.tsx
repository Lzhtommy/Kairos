import { useEffect, useMemo, useRef, useState } from "react"
import { cn } from "@/lib/utils"

type Point = { t: string; v: number }

const H = 260
const M = { l: 48, r: 12, t: 12, b: 24 }

/** 把净值域切成 ~4 段的"好看"步长（1/2/2.5/5 × 10^k）。 */
function niceStep(range: number, target = 4): number {
  const raw = range / target
  const mag = 10 ** Math.floor(Math.log10(raw || 1))
  for (const m of [1, 2, 2.5, 5, 10]) {
    if (raw <= m * mag) return m * mag
  }
  return 10 * mag
}

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

/**
 * 回测净值曲线：Y 轴收益率(%)、X 轴时间（事件模式为持有日 D0..DN）、
 * 网格线、悬浮十字线 + 数值提示、基准虚线对比。
 */
export function BacktestChart({
  curve,
  benchmark = [],
  benchmarkName = "基准",
}: {
  curve: Point[]
  benchmark?: Point[]
  benchmarkName?: string
}) {
  const { ref, width } = useContainerWidth()
  const [hover, setHover] = useState<number | null>(null)

  const model = useMemo(() => {
    if (curve.length < 2 || width < M.l + M.r + 40) return null
    const all = [...curve.map((p) => p.v), ...benchmark.map((p) => p.v)]
    let lo = Math.min(...all)
    let hi = Math.max(...all)
    const pad = (hi - lo || 0.02) * 0.06
    lo -= pad
    hi += pad
    const iw = width - M.l - M.r
    const ih = H - M.t - M.b
    const x = (i: number, n: number) => M.l + (i / (n - 1)) * iw
    const y = (v: number) => M.t + (1 - (v - lo) / (hi - lo)) * ih

    // Y 刻度：以收益率 % 为单位取整步长
    const pctLo = (lo - 1) * 100
    const pctHi = (hi - 1) * 100
    const step = niceStep(pctHi - pctLo)
    const yTicks: number[] = []
    for (let p = Math.ceil(pctLo / step) * step; p <= pctHi + 1e-9; p += step) {
      yTicks.push(p)
    }
    // X 刻度：均匀取 ~6 个
    const n = curve.length
    const xCount = Math.min(6, n)
    const xTicks = Array.from({ length: xCount }, (_, k) =>
      Math.round((k / (xCount - 1)) * (n - 1)),
    )
    const benchByT = new Map(benchmark.map((p) => [p.t, p.v]))
    return { lo, hi, x, y, yTicks, xTicks, benchByT, iw }
  }, [curve, benchmark, width])

  if (curve.length < 2) return null

  const up = curve[curve.length - 1].v >= curve[0].v
  const lineColor = up ? "var(--color-up, #16a34a)" : "var(--color-down, #dc2626)"

  const toPath = (pts: Point[], x: (i: number, n: number) => number, y: (v: number) => number) =>
    pts.map((p, i) => `${i ? "L" : "M"}${x(i, pts.length).toFixed(1)},${y(p.v).toFixed(1)}`).join("")

  const fmtDate = (t: string) => (t.startsWith("D") ? t : t.slice(2)) // 2026-03-05 → 26-03-05

  const hoverPoint = hover != null && model ? curve[hover] : null
  const hoverBench =
    hoverPoint && model ? model.benchByT.get(hoverPoint.t) : undefined

  return (
    <div ref={ref} className="relative w-full select-none">
      {model && (
        <svg
          width={width}
          height={H}
          onMouseMove={(e) => {
            const rect = e.currentTarget.getBoundingClientRect()
            const px = e.clientX - rect.left
            const idx = Math.round(((px - M.l) / model.iw) * (curve.length - 1))
            setHover(Math.min(curve.length - 1, Math.max(0, idx)))
          }}
          onMouseLeave={() => setHover(null)}
        >
          {/* 网格 + Y 轴刻度（收益率%） */}
          {model.yTicks.map((p) => {
            const yy = model.y(1 + p / 100)
            return (
              <g key={p}>
                <line
                  x1={M.l} x2={width - M.r} y1={yy} y2={yy}
                  stroke="var(--color-border, #e5e7eb)" strokeWidth="1"
                  opacity={Math.abs(p) < 1e-9 ? 0.9 : 0.45}
                />
                <text
                  x={M.l - 6} y={yy + 3} textAnchor="end"
                  className="fill-muted-foreground font-mono" fontSize="10"
                >
                  {p > 0 ? "+" : ""}{Math.abs(p) < 10 ? p.toFixed(1).replace(/\.0$/, "") : Math.round(p)}%
                </text>
              </g>
            )
          })}
          {/* X 轴刻度（时间 / 持有日） */}
          {model.xTicks.map((i) => (
            <text
              key={i}
              x={model.x(i, curve.length)} y={H - 6} textAnchor="middle"
              className="fill-muted-foreground font-mono" fontSize="10"
            >
              {fmtDate(curve[i].t)}
            </text>
          ))}
          {/* 基准（虚线） */}
          {benchmark.length > 1 && (
            <path
              d={toPath(benchmark, model.x, model.y)}
              fill="none" stroke="var(--color-muted-foreground, #9ca3af)"
              strokeWidth="1" strokeDasharray="3 3" opacity="0.65"
            />
          )}
          {/* 策略曲线 */}
          <path d={toPath(curve, model.x, model.y)} fill="none" stroke={lineColor} strokeWidth="1.5" />
          {/* 悬浮十字线 */}
          {hoverPoint && (
            <g>
              <line
                x1={model.x(hover!, curve.length)} x2={model.x(hover!, curve.length)}
                y1={M.t} y2={H - M.b}
                stroke="var(--color-muted-foreground, #9ca3af)" strokeWidth="1" strokeDasharray="2 2" opacity="0.7"
              />
              <circle
                cx={model.x(hover!, curve.length)} cy={model.y(hoverPoint.v)} r="3"
                fill={lineColor}
              />
            </g>
          )}
        </svg>
      )}
      {/* 悬浮数值 */}
      {hoverPoint && model && (
        <div
          className="pointer-events-none absolute top-2 rounded-md border border-border bg-popover px-2.5 py-1.5 text-xs shadow-md"
          style={{
            left: Math.min(Math.max(model.x(hover!, curve.length) + 10, M.l), width - 150),
          }}
        >
          <p className="font-mono tabular-nums text-muted-foreground">{hoverPoint.t}</p>
          <p className="font-mono tabular-nums">
            <span className="text-muted-foreground">策略 </span>
            <span className={cn(hoverPoint.v >= 1 ? "text-up" : "text-down")}>
              {hoverPoint.v >= 1 ? "+" : ""}{((hoverPoint.v - 1) * 100).toFixed(2)}%
            </span>
          </p>
          {hoverBench != null && (
            <p className="font-mono tabular-nums text-muted-foreground">
              {benchmarkName} {hoverBench >= 1 ? "+" : ""}{((hoverBench - 1) * 100).toFixed(2)}%
            </p>
          )}
        </div>
      )}
      {/* 图例 */}
      <div className="mt-1 flex items-center gap-4 px-1 text-[11px] text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-4 rounded" style={{ background: lineColor }} />
          策略
        </span>
        {benchmark.length > 1 && (
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-0 w-4 border-t border-dashed border-muted-foreground" />
            {benchmarkName}
          </span>
        )}
      </div>
    </div>
  )
}
