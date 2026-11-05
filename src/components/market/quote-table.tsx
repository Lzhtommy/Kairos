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
import {
  CaretUpDown,
  CaretUp,
  CaretDown,
} from "@phosphor-icons/react"
import type { Stock } from "@/lib/mock-data"
import { changePct } from "@/lib/use-live-quotes"
import { ChangeBadge, PriceCell, Sparkline } from "@/components/market/price-change"
import { TablePager } from "@/components/ui/table-pager"
import { cn } from "@/lib/utils"

/** 雪球个股页，格式 SH600519 / SZ000001 / BJ920000，北交所也支持。 */
function stockDetailUrl(s: Stock) {
  return `https://xueqiu.com/S/${s.market}${s.code}`
}

/** extraColumns 插在涨跌幅之后；调用方需传稳定引用（模块常量或 useMemo）。 */
export function QuoteTable<T extends Stock = Stock>({
  data,
  dense = true,
  pageSize = 50,
  extraColumns,
  defaultSorting = [{ id: "marketCap", desc: true }],
}: {
  data: T[]
  dense?: boolean
  pageSize?: number
  extraColumns?: ColumnDef<T>[]
  defaultSorting?: SortingState
}) {
  const [sorting, setSorting] = useState<SortingState>(defaultSorting)
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize })

  const columns = useMemo<ColumnDef<T>[]>(
    () => [
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
      ...(extraColumns ?? []),
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
    [extraColumns],
  )

  const table = useReactTable({
    data,
    columns,
    state: { sorting, pagination },
    onSortingChange: (updater) => {
      setSorting(updater)
      setPagination((p) => ({ ...p, pageIndex: 0 }))
    },
    onPaginationChange: setPagination,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    // 行情每分钟轮询会换新 data 引用，默认的 auto-reset 会把用户踢回第一页
    autoResetPageIndex: false,
  })

  // 数据变少（换 tab、筛选收紧）时把页码收回到有效范围
  const pageCount = table.getPageCount()
  useEffect(() => {
    setPagination((p) =>
      pageCount > 0 && p.pageIndex >= pageCount ? { ...p, pageIndex: pageCount - 1 } : p,
    )
  }, [pageCount])

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
              onClick={() => window.open(stockDetailUrl(row.original), "_blank", "noopener")}
              title={`在雪球查看 ${row.original.name}`}
              className="group cursor-pointer border-b border-border/60 transition-colors hover:bg-muted/50"
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
      <TablePager
        total={data.length}
        pageIndex={pagination.pageIndex}
        pageSize={pagination.pageSize}
        onPageIndex={(i) => table.setPageIndex(i)}
        onPageSize={(n) => table.setPageSize(n)}
      />
    </div>
  )
}

