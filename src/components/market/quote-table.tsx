import { useMemo, useState } from "react"
import {
  type ColumnDef,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table"
import { CaretUpDown, CaretUp, CaretDown, Star } from "@phosphor-icons/react"
import type { Stock } from "@/lib/mock-data"
import { changePct } from "@/lib/use-live-quotes"
import { ChangeBadge, PriceCell, Sparkline } from "@/components/market/price-change"
import { cn } from "@/lib/utils"

function StarToggle({ code }: { code: string }) {
  const [active, setActive] = useState(false)
  return (
    <button
      onClick={(e) => {
        e.stopPropagation()
        setActive((v) => !v)
      }}
      aria-label={active ? "移出自选" : "加入自选"}
      className="flex size-6 items-center justify-center rounded text-muted-foreground hover:text-primary"
    >
      <Star weight={active ? "fill" : "regular"} className={cn("size-3.5", active && "text-primary")} data-code={code} />
    </button>
  )
}

export function QuoteTable({ data, dense = true }: { data: Stock[]; dense?: boolean }) {
  const [sorting, setSorting] = useState<SortingState>([{ id: "marketCap", desc: true }])

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
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })

  return (
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
  )
}
