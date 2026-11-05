import type { ColumnDef } from "@tanstack/react-table"
import { ArrowDown, ArrowUp, Flame } from "@phosphor-icons/react"
import type { Stock } from "@/lib/mock-data"
import { ChangeBadge } from "@/components/market/price-change"
import { cn } from "@/lib/utils"

/** 开盘涨跌幅 + 东财人气，自选股与选股结果共用 */
export type OpenHotFields = {
  /** 交易日开盘价相对前收的涨跌幅%；未开盘为 null */
  openPct: number | null
  /** 东财人气排名：交易日是今天时为实时排名，过去的交易日为当天的日排名 */
  hotRank: number | null
  /** 上一交易日的人气排名（落库的日排名） */
  hotRankPrev: number | null
}

export type OpenHotStock = Stock & OpenHotFields

/** 返回新数组，调用方要放进模块常量或 useMemo（QuoteTable 以引用判断列是否变化） */
export function openHotColumns<T extends OpenHotStock>(): ColumnDef<T>[] {
  return [
    {
      id: "openPct",
      header: "开盘涨跌",
      // 未开盘的排到最后（降序时）
      accessorFn: (r) => r.openPct ?? -Infinity,
      cell: ({ row }) =>
        row.original.openPct == null ? (
          <span className="text-xs text-muted-foreground" title="交易日未开盘，或缺少当日日 K">
            —
          </span>
        ) : (
          <ChangeBadge pct={row.original.openPct} />
        ),
    },
    {
      id: "hotRankPrev",
      header: "昨日人气",
      accessorFn: (r) => r.hotRankPrev ?? Infinity,
      cell: ({ row }) => <HotRank rank={row.original.hotRankPrev} />,
    },
    {
      id: "hotRank",
      header: "当日人气",
      accessorFn: (r) => r.hotRank ?? Infinity,
      cell: ({ row }) => {
        const { hotRank, hotRankPrev } = row.original
        // 名次数字变小 = 人气上升
        const delta = hotRank != null && hotRankPrev != null ? hotRankPrev - hotRank : null
        return (
          <span className="inline-flex items-center gap-1.5">
            <HotRank rank={hotRank} />
            {delta != null && delta !== 0 && (
              <span
                className={cn(
                  "inline-flex items-center font-mono text-[11px] tabular-nums",
                  delta > 0 ? "text-up" : "text-down",
                )}
              >
                {delta > 0 ? (
                  <ArrowUp weight="bold" className="size-2.5" />
                ) : (
                  <ArrowDown weight="bold" className="size-2.5" />
                )}
                {Math.abs(delta)}
              </span>
            )}
          </span>
        )
      },
    },
  ]
}

function HotRank({ rank }: { rank: number | null }) {
  if (rank == null) return <span className="text-xs text-muted-foreground">—</span>
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 font-mono text-sm tabular-nums",
        rank <= 100 ? "text-up" : "text-foreground",
      )}
    >
      {rank <= 100 && <Flame weight="fill" className="size-3" />}
      {rank}
    </span>
  )
}
