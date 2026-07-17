import { useEffect, useMemo, useState } from "react"
import {
  type ColumnDef,
  type PaginationState,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  CaretUpDown,
  CaretUp,
  CaretDown,
  CaretLeft,
  CaretRight,
  CaretDoubleLeft,
  CaretDoubleRight,
  Star,
} from "@phosphor-icons/react"
import type { Stock } from "@/lib/mock-data"
import { changePct } from "@/lib/use-live-quotes"
import { fetchWatchlist, addToWatchlist, removeFromWatchlist } from "@/api/watchlist"
import { ChangeBadge, PriceCell, Sparkline } from "@/components/market/price-change"
import { cn } from "@/lib/utils"

function StarToggle({ code }: { code: string }) {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ["watchlist"],
    queryFn: fetchWatchlist,
    staleTime: 30_000,
  })
  const active = data?.codes.includes(code) ?? false

  const mutation = useMutation({
    mutationFn: () => (active ? removeFromWatchlist(code) : addToWatchlist(code)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["watchlist"] })
    },
  })

  return (
    <button
      onClick={(e) => {
        e.stopPropagation()
        mutation.mutate()
      }}
      disabled={mutation.isPending}
      aria-label={active ? "移出自选" : "加入自选"}
      className="flex size-6 items-center justify-center rounded text-muted-foreground hover:text-primary"
    >
      <Star weight={active ? "fill" : "regular"} className={cn("size-3.5", active && "text-primary")} data-code={code} />
    </button>
  )
}

const PAGE_SIZE_OPTIONS = [10, 20, 50, 100]

export function QuoteTable({
  data,
  dense = true,
  pageSize = 20,
}: {
  data: Stock[]
  dense?: boolean
  pageSize?: number
}) {
  const [sorting, setSorting] = useState<SortingState>([{ id: "marketCap", desc: true }])
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize })

  const columns = useMemo<ColumnDef<Stock>[]>(
    () => [
      {
        id: "star",
        header: "",
        size: 32,
        cell: ({ row }) => <StarToggle code={row.original.code} />,
      },
      {
        id: "name",
        header: "名称 / 代码",
        accessorFn: (r) => r.name,
        cell: ({ row }) => (
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-foreground">{row.original.name}</p>
            <p className="font-mono text-[11px] text-muted-foreground">
              {row.original.market}·{row.original.code}
            </p>
          </div>
        ),
      },
      {
        id: "price",
        header: "最新价",
        accessorFn: (r) => r.price,
        sortingFn: "basic",
        cell: ({ row }) => (
          <PriceCell price={row.original.price} changePct={changePct(row.original)} className="text-sm" />
        ),
      },
      {
        id: "changePct",
        header: "涨跌幅",
        accessorFn: (r) => changePct(r),
        cell: ({ row }) => <ChangeBadge pct={changePct(row.original)} />,
      },
      {
        id: "turnover",
        header: "成交额(亿)",
        accessorFn: (r) => r.turnover,
        cell: ({ getValue }) => (
          <span className="font-mono text-sm tabular-nums text-foreground">
            {(getValue() as number).toFixed(2)}
          </span>
        ),
      },
      {
        id: "turnoverRate",
        header: "换手率",
        accessorFn: (r) => r.turnoverRate,
        cell: ({ getValue }) => (
          <span className="font-mono text-sm tabular-nums text-muted-foreground">
            {(getValue() as number).toFixed(2)}%
          </span>
        ),
      },
      {
        id: "pe",
        header: "市盈率",
        accessorFn: (r) => r.pe ?? -1,
        cell: ({ row }) => (
          <span className="font-mono text-sm tabular-nums text-muted-foreground">
            {row.original.pe ? row.original.pe.toFixed(1) : "亏损"}
          </span>
        ),
      },
      {
        id: "marketCap",
        header: "总市值(亿)",
        accessorFn: (r) => r.marketCap,
        cell: ({ getValue }) => (
          <span className="font-mono text-sm tabular-nums text-foreground">
            {(getValue() as number).toLocaleString("zh-CN")}
          </span>
        ),
      },
      {
        id: "spark",
        header: "走势",
        enableSorting: false,
        cell: ({ row }) => (
          <Sparkline data={row.original.spark} changePct={changePct(row.original)} />
        ),
      },
    ],
    [],
  )

  const table = useReactTable({
    data,
    columns,
    state: { sorting, pagination },
    onSortingChange: setSorting,
    onPaginationChange: setPagination,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    // Live data re-fetches a new array every ~60s; don't snap back to page 1.
    autoResetPageIndex: false,
  })

  const pageCount = table.getPageCount()
  const { pageIndex, pageSize: currentPageSize } = table.getState().pagination

  // Clamp the page when the row set shrinks (e.g. filters tighten) so we never
  // sit on an out-of-range empty page.
  useEffect(() => {
    if (pageCount > 0 && pageIndex > pageCount - 1) {
      table.setPageIndex(pageCount - 1)
    }
  }, [pageCount, pageIndex, table])

  const total = data.length
  const from = total === 0 ? 0 : pageIndex * currentPageSize + 1
  const to = Math.min((pageIndex + 1) * currentPageSize, total)

  return (
    <div className="w-full">
    <div className="w-full overflow-x-auto scrollbar-thin">
      <table className="w-full border-collapse text-left">
        <thead>
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id} className="border-b border-border">
              {hg.headers.map((header) => {
                const sortable = header.column.getCanSort()
                const sortDir = header.column.getIsSorted()
                return (
                  <th
                    key={header.id}
                    onClick={header.column.getToggleSortingHandler()}
                    className={cn(
                      "whitespace-nowrap px-3 py-2 text-xs font-medium text-muted-foreground",
                      sortable && "cursor-pointer select-none hover:text-foreground",
                    )}
                  >
                    <span className="inline-flex items-center gap-1">
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      {sortable &&
                        (sortDir === "asc" ? (
                          <CaretUp className="size-3" />
                        ) : sortDir === "desc" ? (
                          <CaretDown className="size-3" />
                        ) : (
                          <CaretUpDown className="size-3 opacity-40" />
                        ))}
                    </span>
                  </th>
                )
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr
              key={row.id}
              className="group border-b border-border/60 transition-colors hover:bg-muted/50"
            >
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id} className={cn("px-3", dense ? "py-1.5" : "py-2.5")}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-3 py-2.5">
        <p className="text-xs text-muted-foreground">
          共 <span className="font-mono text-foreground">{total}</span> 条
          {total > 0 && (
            <>
              ，当前 <span className="font-mono text-foreground">{from}-{to}</span>
            </>
          )}
        </p>
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            每页
            <select
              value={currentPageSize}
              onChange={(e) => table.setPageSize(Number(e.target.value))}
              className="rounded-md border border-border bg-background px-1.5 py-1 text-xs text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            >
              {PAGE_SIZE_OPTIONS.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
            条
          </label>
          <span className="font-mono text-xs tabular-nums text-muted-foreground">
            {pageIndex + 1} / {Math.max(pageCount, 1)}
          </span>
          <div className="flex items-center gap-0.5">
            {[
              { icon: CaretDoubleLeft, label: "首页", onClick: () => table.setPageIndex(0), disabled: !table.getCanPreviousPage() },
              { icon: CaretLeft, label: "上一页", onClick: () => table.previousPage(), disabled: !table.getCanPreviousPage() },
              { icon: CaretRight, label: "下一页", onClick: () => table.nextPage(), disabled: !table.getCanNextPage() },
              { icon: CaretDoubleRight, label: "末页", onClick: () => table.setPageIndex(pageCount - 1), disabled: !table.getCanNextPage() },
            ].map(({ icon: Icon, label, onClick, disabled }) => (
              <button
                key={label}
                onClick={onClick}
                disabled={disabled}
                aria-label={label}
                className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
              >
                <Icon className="size-4" />
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
