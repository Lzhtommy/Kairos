import { useEffect, useState, type ReactNode } from "react"
import { CaretDoubleLeft, CaretDoubleRight, CaretLeft, CaretRight } from "@phosphor-icons/react"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

export const PAGE_SIZE_OPTIONS = [20, 50, 100]

/** 客户端分页状态。resetKey 变化（筛选条件变了）回到第一页；数据变少时页码收回有效范围。 */
export function usePagedRows<T>(rows: T[], resetKey: string, initialPageSize = 50) {
  const [pageIndex, setPageIndex] = useState(0)
  const [pageSize, setPageSize] = useState(initialPageSize)
  useEffect(() => setPageIndex(0), [resetKey])
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize))
  const index = Math.min(pageIndex, pageCount - 1)
  return {
    pageRows: rows.slice(index * pageSize, (index + 1) * pageSize),
    pager: {
      total: rows.length,
      pageIndex: index,
      pageSize,
      onPageIndex: setPageIndex,
      onPageSize: (n: number) => {
        setPageSize(n)
        setPageIndex(0)
      },
    },
  }
}

/** 表格底部分页条；条数不超过最小每页条数时不显示。 */
export function TablePager({
  total,
  pageIndex,
  pageSize,
  onPageIndex,
  onPageSize,
}: {
  total: number
  pageIndex: number
  pageSize: number
  onPageIndex: (i: number) => void
  onPageSize: (n: number) => void
}) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  if (pageCount <= 1 && total <= PAGE_SIZE_OPTIONS[0]) return null
  const canPrev = pageIndex > 0
  const canNext = pageIndex < pageCount - 1
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-3 py-2">
      <span className="text-xs text-muted-foreground">
        共 <span className="font-mono tabular-nums text-foreground">{total}</span> 条 · 第{" "}
        <span className="font-mono tabular-nums text-foreground">{pageIndex + 1}</span>
        {" / "}
        <span className="font-mono tabular-nums text-foreground">{pageCount}</span> 页
      </span>
      <div className="flex items-center gap-2">
        <Select value={String(pageSize)} onValueChange={(v) => onPageSize(Number(v))}>
          <SelectTrigger size="sm" className="text-xs">
            <SelectValue>{(v: string) => `${v} 条/页`}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {PAGE_SIZE_OPTIONS.map((n) => (
              <SelectItem key={n} value={String(n)}>
                {n} 条/页
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="flex items-center gap-1">
          <PagerButton label="第一页" disabled={!canPrev} onClick={() => onPageIndex(0)}>
            <CaretDoubleLeft className="size-3.5" />
          </PagerButton>
          <PagerButton label="上一页" disabled={!canPrev} onClick={() => onPageIndex(pageIndex - 1)}>
            <CaretLeft className="size-3.5" />
          </PagerButton>
          <PagerButton label="下一页" disabled={!canNext} onClick={() => onPageIndex(pageIndex + 1)}>
            <CaretRight className="size-3.5" />
          </PagerButton>
          <PagerButton
            label="最后一页"
            disabled={!canNext}
            onClick={() => onPageIndex(pageCount - 1)}
          >
            <CaretDoubleRight className="size-3.5" />
          </PagerButton>
        </div>
      </div>
    </div>
  )
}

function PagerButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string
  disabled: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      className="flex size-7 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
    >
      {children}
    </button>
  )
}
