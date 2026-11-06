import { useEffect, useMemo, useRef, useState } from "react"

const M = { l: 48, r: 12, t: 10, b: 22 }

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

/** 单序列折线图（日期 + 数值），悬停显示该点。用于个股收盘价和宏观序列。 */
export function LineChart({
  points,
  height = 220,
  format = (v) => v.toFixed(2),
  zeroLine = false,
}: {
  points: [string, number][]
  height?: number
  format?: (v: number) => string
  zeroLine?: boolean
}) {
  const { ref, width } = useContainerWidth()
  const [hover, setHover] = useState<number | null>(null)

  const model = useMemo(() => {
    const n = points.length
    if (n < 2 || width < M.l + M.r + 40) return null
    const vals = points.map((p) => p[1])
    let lo = Math.min(...vals, ...(zeroLine ? [0] : []))
    let hi = Math.max(...vals, ...(zeroLine ? [0] : []))
    const pad = (hi - lo || Math.abs(hi) * 0.1 || 1) * 0.06
    lo -= pad
    hi += pad
    const iw = width - M.l - M.r
    const ih = height - M.t - M.b
    const x = (i: number) => M.l + (i / (n - 1)) * iw
    const y = (v: number) => M.t + (1 - (v - lo) / (hi - lo)) * ih
    let path = ""
    for (let i = 0; i < n; i++) path += `${i ? "L" : "M"}${x(i).toFixed(1)},${y(points[i][1]).toFixed(1)}`
    const step = niceStep(hi - lo)
    const yTicks: number[] = []
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) yTicks.push(v)
    const xCount = Math.min(6, n)
    const xTicks = Array.from({ length: xCount }, (_, k) => Math.round((k / (xCount - 1)) * (n - 1)))
    return { x, y, iw, path, yTicks, xTicks, step, lo, hi }
  }, [points, width, height, zeroLine])

  const hp = hover != null ? points[hover] : null
  const tick = (v: number) => (model && model.step < 1 ? v.toFixed(model.step < 0.1 ? 2 : 1) : String(Math.round(v)))

  return (
    <div ref={ref} className="relative w-full select-none">
      {model ? (
        <svg
          width={width}
          height={height}
          onMouseMove={(e) => {
            const rect = e.currentTarget.getBoundingClientRect()
            const idx = Math.round(((e.clientX - rect.left - M.l) / model.iw) * (points.length - 1))
            setHover(Math.min(points.length - 1, Math.max(0, idx)))
          }}
          onMouseLeave={() => setHover(null)}
        >
          {model.yTicks.map((v) => (
            <g key={v}>
              <line
                x1={M.l} x2={width - M.r} y1={model.y(v)} y2={model.y(v)}
                stroke="var(--color-border, #e5e7eb)" strokeWidth="1"
                opacity={zeroLine && Math.abs(v) < 1e-9 ? 1 : 0.45}
              />
              <text x={M.l - 6} y={model.y(v) + 3} textAnchor="end" className="fill-muted-foreground font-mono" fontSize="10">
                {tick(v)}
              </text>
            </g>
          ))}
          {model.xTicks.map((i, k) => (
            <text
              key={i}
              x={model.x(i)} y={height - 6}
              textAnchor={k === 0 ? "start" : k === model.xTicks.length - 1 ? "end" : "middle"}
              className="fill-muted-foreground font-mono" fontSize="10"
            >
              {points[i][0].slice(0, 7)}
            </text>
          ))}
          <path d={model.path} fill="none" className="stroke-foreground" strokeWidth="1.5" />
          {hp && (
            <g>
              <line
                x1={model.x(hover!)} x2={model.x(hover!)} y1={M.t} y2={height - M.b}
                stroke="var(--color-muted-foreground, #9ca3af)" strokeWidth="1" strokeDasharray="2 2" opacity="0.7"
              />
              <circle cx={model.x(hover!)} cy={model.y(hp[1])} r="3" className="fill-foreground" />
            </g>
          )}
        </svg>
      ) : (
        <div style={{ height }} className="flex items-center justify-center text-xs text-muted-foreground">
          {points.length < 2 ? "暂无数据" : ""}
        </div>
      )}
      {hp && model && (
        <div
          className="pointer-events-none absolute top-1 rounded-md border border-border bg-popover px-2 py-1 text-xs shadow-md"
          style={{ left: Math.min(Math.max(model.x(hover!) + 10, M.l), width - 130) }}
        >
          <p className="font-mono tabular-nums text-muted-foreground">{hp[0]}</p>
          <p className="font-mono tabular-nums">{format(hp[1])}</p>
        </div>
      )}
    </div>
  )
}
