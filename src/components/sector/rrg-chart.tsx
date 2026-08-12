import { useEffect, useMemo, useRef, useState } from "react"
import { cn } from "@/lib/utils"

export type RRGSector = {
  code: string
  name: string
  trail: [number, number][]
  rel4w: number | null
}

const H = 440
const M = { l: 44, r: 16, t: 16, b: 32 }

// 象限：x = RS-Ratio（>100 强于基准），y = RS-Momentum（>100 相对强度在改善）
// A 股配色习惯：领先=红（涨）、落后=绿（跌）
const QUADRANTS = [
  { test: (x: number, y: number) => x >= 100 && y >= 100, label: "领先", color: "var(--color-up)" },
  { test: (x: number, y: number) => x < 100 && y >= 100, label: "改善", color: "#3b82f6" },
  { test: (x: number, y: number) => x >= 100 && y < 100, label: "转弱", color: "#f59e0b" },
  { test: () => true, label: "落后", color: "var(--color-down)" },
]

function quadrantOf(x: number, y: number) {
  return QUADRANTS.find((q) => q.test(x, y))!
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
 * RRG 相对轮动图（周频）：每个板块最近 6 周的 (RS-Ratio, RS-Momentum) 轨迹，
 * 大点为本周位置。通常按 改善 → 领先 → 转弱 → 落后 顺时针轮动。
 */
export function RRGChart({ sectors }: { sectors: RRGSector[] }) {
  const { ref, width } = useContainerWidth()
  const [hover, setHover] = useState<string | null>(null)

  const model = useMemo(() => {
    if (!sectors.length || width < 200) return null
    const xs = sectors.flatMap((s) => s.trail.map((p) => p[0]))
    const ys = sectors.flatMap((s) => s.trail.map((p) => p[1]))
    // 以 100 为中心取对称域，保证四个象限都可见
    const spanX = Math.max(1.2, ...xs.map((v) => Math.abs(v - 100))) * 1.15
    const spanY = Math.max(1.2, ...ys.map((v) => Math.abs(v - 100))) * 1.15
    const x = (v: number) => M.l + ((v - (100 - spanX)) / (2 * spanX)) * (width - M.l - M.r)
    const y = (v: number) => M.t + (1 - (v - (100 - spanY)) / (2 * spanY)) * (H - M.t - M.b)
    // 直接标签：离中心最远的 12 个（其余靠悬停）
    const labeled = new Set(
      sectors
        .slice()
        .sort((a, b) => {
          const da = a.trail.at(-1)!
          const db = b.trail.at(-1)!
          return (
            Math.hypot(db[0] - 100, db[1] - 100) - Math.hypot(da[0] - 100, da[1] - 100)
          )
        })
        .slice(0, 12)
        .map((s) => s.code),
    )
    return { x, y, labeled }
  }, [sectors, width])

  const hovered = hover ? sectors.find((s) => s.code === hover) : null

  return (
    <div ref={ref} className="relative w-full select-none">
      {model && (
        <svg width={width} height={H}>
          {/* 象限底色 + 中轴线 */}
          <rect x={model.x(100)} y={M.t} width={width - M.r - model.x(100)} height={model.y(100) - M.t} fill="var(--color-up)" opacity="0.04" />
          <rect x={M.l} y={M.t} width={model.x(100) - M.l} height={model.y(100) - M.t} fill="#3b82f6" opacity="0.04" />
          <rect x={model.x(100)} y={model.y(100)} width={width - M.r - model.x(100)} height={H - M.b - model.y(100)} fill="#f59e0b" opacity="0.04" />
          <rect x={M.l} y={model.y(100)} width={model.x(100) - M.l} height={H - M.b - model.y(100)} fill="var(--color-down)" opacity="0.04" />
          <line x1={model.x(100)} x2={model.x(100)} y1={M.t} y2={H - M.b} stroke="var(--color-border)" strokeWidth="1" />
          <line x1={M.l} x2={width - M.r} y1={model.y(100)} y2={model.y(100)} stroke="var(--color-border)" strokeWidth="1" />
          {/* 象限角标 */}
          {(
            [
              ["领先", width - M.r - 6, M.t + 14, "end"],
              ["改善", M.l + 6, M.t + 14, "start"],
              ["转弱", width - M.r - 6, H - M.b - 8, "end"],
              ["落后", M.l + 6, H - M.b - 8, "start"],
            ] as const
          ).map(([label, x, y, anchor]) => (
            <text key={label} x={x} y={y} textAnchor={anchor} fontSize="11" className="fill-muted-foreground" opacity="0.7">
              {label}
            </text>
          ))}
          {/* 轴标签 */}
          <text x={width - M.r} y={H - 8} textAnchor="end" fontSize="10" className="fill-muted-foreground">
            RS-Ratio →
          </text>
          <text x={12} y={M.t + 4} fontSize="10" className="fill-muted-foreground" transform={`rotate(-90 12 ${M.t + 4})`} textAnchor="end">
            RS-Momentum →
          </text>
          {/* 轨迹 + 当前点 */}
          {sectors.map((s) => {
            const last = s.trail.at(-1)!
            const q = quadrantOf(last[0], last[1])
            const dim = hover !== null && hover !== s.code
            return (
              <g
                key={s.code}
                opacity={dim ? 0.18 : 1}
                onMouseEnter={() => setHover(s.code)}
                onMouseLeave={() => setHover(null)}
                className="cursor-pointer"
              >
                <polyline
                  points={s.trail.map((p) => `${model.x(p[0]).toFixed(1)},${model.y(p[1]).toFixed(1)}`).join(" ")}
                  fill="none"
                  stroke={q.color}
                  strokeWidth="1"
                  opacity="0.45"
                />
                {s.trail.slice(0, -1).map((p, i) => (
                  <circle key={i} cx={model.x(p[0])} cy={model.y(p[1])} r="1.5" fill={q.color} opacity="0.45" />
                ))}
                <circle cx={model.x(last[0])} cy={model.y(last[1])} r="4" fill={q.color} />
                {(model.labeled.has(s.code) || hover === s.code) && (
                  <text
                    x={model.x(last[0]) + 6}
                    y={model.y(last[1]) + 3.5}
                    fontSize="10.5"
                    className="fill-foreground"
                  >
                    {s.name}
                  </text>
                )}
              </g>
            )
          })}
        </svg>
      )}
      {hovered && model && (
        <div
          className="pointer-events-none absolute top-2 left-1/2 -translate-x-1/2 rounded-md border border-border bg-popover px-2.5 py-1.5 text-xs shadow-md"
        >
          <span className="font-medium">{hovered.name}</span>
          <span className="ml-2 font-mono tabular-nums text-muted-foreground">
            RS {hovered.trail.at(-1)![0].toFixed(1)} · 动量 {hovered.trail.at(-1)![1].toFixed(1)}
          </span>
          {hovered.rel4w != null && (
            <span className={cn("ml-2 font-mono tabular-nums", hovered.rel4w >= 0 ? "text-up" : "text-down")}>
              4周相对 {hovered.rel4w >= 0 ? "+" : ""}
              {hovered.rel4w.toFixed(1)}%
            </span>
          )}
        </div>
      )}
    </div>
  )
}
