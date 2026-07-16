import { cn } from "@/lib/utils"
import { CaretUp, CaretDown, Minus } from "@phosphor-icons/react"

function direction(value: number) {
  if (value > 0) return "up"
  if (value < 0) return "down"
  return "flat"
}

const toneClass = {
  up: "text-up",
  down: "text-down",
  flat: "text-muted-foreground",
} as const

export function ChangeBadge({ pct, className }: { pct: number; className?: string }) {
  const dir = direction(pct)
  const Icon = dir === "up" ? CaretUp : dir === "down" ? CaretDown : Minus
  return (
    <span
      className={cn(
        "inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-xs font-medium font-mono tabular-nums",
        dir === "up" && "bg-up-muted text-up",
        dir === "down" && "bg-down-muted text-down",
        dir === "flat" && "bg-muted text-muted-foreground",
        className,
      )}
    >
      <Icon weight="bold" className="size-2.5" />
      {pct > 0 ? "+" : ""}
      {pct.toFixed(2)}%
    </span>
  )
}

export function PriceCell({
  price,
  changePct,
  className,
}: {
  price: number
  changePct: number
  className?: string
}) {
  const dir = direction(changePct)
  return (
    <span className={cn("font-mono tabular-nums font-medium", toneClass[dir], className)}>
      {price.toFixed(2)}
    </span>
  )
}

export function Sparkline({
  data,
  changePct,
  className,
}: {
  data: number[]
  /** 当日涨跌幅，用于统一线色与价格颜色；缺省时按曲线首尾方向着色 */
  changePct?: number
  className?: string
}) {
  const dir = direction(changePct ?? data[data.length - 1] - data[0])
  const min = Math.min(...data)
  const max = Math.max(...data)
  const range = max - min || 1
  const w = 72
  const h = 24
  const points = data
    .map((v, i) => {
      const x = (i / (data.length - 1)) * w
      const y = h - ((v - min) / range) * h
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(" ")
  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      width={w}
      height={h}
      className={cn("overflow-visible", className)}
      aria-hidden
    >
      <polyline
        points={points}
        fill="none"
        strokeWidth={1.5}
        className={dir === "up" ? "stroke-up" : dir === "down" ? "stroke-down" : "stroke-flat"}
      />
    </svg>
  )
}
