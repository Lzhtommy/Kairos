import { useEffect, useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { DIM_ORDER, type DimSummary, type Grade, type HistoryPoint } from "@/api/research"
import { cn } from "@/lib/utils"
import { fmtDate } from "@/lib/research-format"

// 评级不用红绿（A 股红涨绿跌，会被读成涨跌）：A→D 单色系由深到浅，红色只留给排雷
const GRADE_TONE: Record<Grade, string> = {
  A: "bg-indigo-600 text-white dark:bg-indigo-500",
  B: "bg-indigo-500/15 text-indigo-700 dark:text-indigo-300",
  C: "bg-slate-500/15 text-slate-700 dark:text-slate-300",
  D: "bg-muted text-muted-foreground",
}

export function GradeBadge({ grade, size = "sm" }: { grade: Grade | null; size?: "sm" | "lg" }) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-md font-semibold tabular-nums",
        size === "lg" ? "size-14 text-3xl" : "size-6 text-xs",
        grade ? GRADE_TONE[grade] : "bg-muted text-muted-foreground",
      )}
      title={grade ? `基本面评级 ${grade}` : "数据覆盖不足，未评级"}
    >
      {grade ?? "—"}
    </span>
  )
}

/** 列表里的 7 根迷你竖条：高度 = 维度得分率 */
export function MiniDims({ dims }: { dims: Record<string, DimSummary> }) {
  return (
    <div className="flex h-6 items-end gap-[3px]">
      {DIM_ORDER.map((k) => {
        const d = dims[k]
        const ratio = d && d.score != null ? d.score / d.max : null
        return (
          <div
            key={k}
            className="relative h-full w-[7px] rounded-[2px] bg-muted"
            title={d ? `${d.label} ${d.score == null ? "数据不足" : `${d.score} / ${d.max}`}` : k}
          >
            {ratio != null && (
              <div
                className="absolute inset-x-0 bottom-0 rounded-[2px] bg-indigo-500/80"
                style={{ height: `${Math.max(6, ratio * 100)}%` }}
              />
            )}
          </div>
        )
      })}
    </div>
  )
}

/** 详情页维度横向得分条 */
export function DimensionBars({ dims }: { dims: Record<string, DimSummary & { insufficient?: boolean }> }) {
  return (
    <div className="space-y-2.5">
      {DIM_ORDER.map((k) => {
        const d = dims[k]
        if (!d) return null
        const ratio = d.score != null ? d.score / d.max : 0
        return (
          <div key={k} className="grid grid-cols-[4.5rem_1fr_4.5rem] items-center gap-3 text-sm">
            <span className="text-muted-foreground">{d.label}</span>
            <div className="h-2 overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full bg-indigo-500" style={{ width: `${ratio * 100}%` }} />
            </div>
            <span className="text-right font-mono text-xs tabular-nums text-foreground">
              {d.score == null ? <span className="text-muted-foreground">数据不足</span> : `${d.score.toFixed(1)} / ${d.max}`}
            </span>
          </div>
        )
      })}
    </div>
  )
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

/** 历次报告总分走势（≥2 个点才画） */
export function ScoreTrend({ points, currentId }: { points: HistoryPoint[]; currentId: number }) {
  const { ref, width } = useContainerWidth()
  const pts = points.filter((p) => p.total_score != null)
  const H = 56
  const pad = 6
  if (pts.length < 2) return null
  const xs = (i: number) => pad + (i / (pts.length - 1)) * Math.max(0, width - pad * 2)
  const ys = (v: number) => pad + (1 - v / 100) * (H - pad * 2)
  const d = pts.map((p, i) => `${i ? "L" : "M"}${xs(i)},${ys(p.total_score!)}`).join(" ")
  return (
    <div ref={ref} className="w-full">
      <div className="mb-1 text-xs text-muted-foreground">分数走势（{pts.length} 次）</div>
      {width > 0 && (
        <svg width={width} height={H} className="overflow-visible">
          <path d={d} fill="none" stroke="currentColor" strokeWidth={1.5} className="text-indigo-500" />
          {pts.map((p, i) => (
            <circle
              key={p.id}
              cx={xs(i)}
              cy={ys(p.total_score!)}
              r={p.id === currentId ? 3.5 : 2.5}
              className={p.id === currentId ? "fill-indigo-500" : "fill-background stroke-indigo-500"}
              strokeWidth={1.5}
            >
              <title>{`${fmtDate(p.finished_at)} · ${p.total_score} · ${p.grade ?? "—"}`}</title>
            </circle>
          ))}
        </svg>
      )}
    </div>
  )
}

export function Markdown({ children }: { children: string }) {
  return (
    <div
      className={cn(
        "space-y-3 text-sm leading-7 text-foreground/90",
        "[&_ul]:list-disc [&_ul]:space-y-1 [&_ul]:pl-5 [&_ol]:list-decimal [&_ol]:pl-5",
        "[&_strong]:font-semibold [&_strong]:text-foreground",
        "[&_table]:w-full [&_table]:text-xs [&_td]:border-b [&_td]:border-border [&_td]:py-1.5 [&_th]:py-1.5 [&_th]:text-left [&_th]:font-medium",
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  )
}
