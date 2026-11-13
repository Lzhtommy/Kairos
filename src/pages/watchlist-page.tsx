import { type ReactNode, useEffect, useRef, useState } from "react"
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type { ColumnDef } from "@tanstack/react-table"
import { toast } from "sonner"
import {
  ArrowCounterClockwise,
  ArrowsClockwise,
  CaretLeft,
  CaretRight,
  CircleNotch,
  Trash,
  UploadSimple,
} from "@phosphor-icons/react"
import {
  deleteWatchlistImport,
  fetchWatchlistImports,
  fetchWatchlistItems,
  importWatchlist,
  type Board,
  type WatchlistStock,
} from "@/api/watchlist"
import { QuoteTable } from "@/components/market/quote-table"
import { openHotColumns } from "@/components/market/open-hot-columns"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { cn } from "@/lib/utils"

const BOARDS: Array<"all" | Board> = ["all", "主板", "创业板", "科创板"]

// 模块常量：QuoteTable 以引用判断列是否变化
const EXTRA_COLUMNS: ColumnDef<WatchlistStock>[] = [
  ...openHotColumns<WatchlistStock>(),
  {
    id: "board",
    header: "板块",
    accessorFn: (r) => r.board,
    cell: ({ row }) => (
      <div className="min-w-0">
        <p className="text-sm text-foreground">{row.original.board}</p>
        <p className="truncate text-[11px] text-muted-foreground">{row.original.industry}</p>
      </div>
    ),
  },
  {
    id: "quantity",
    header: "买入数量",
    accessorFn: (r) => r.quantity,
    cell: ({ getValue }) => (
      <span className="font-mono text-sm tabular-nums text-muted-foreground">
        {(getValue() as number).toLocaleString("zh-CN")}
      </span>
    ),
  },
]

function DayStep({
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
      title={label}
      className="flex size-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
    >
      {children}
    </button>
  )
}

/** 空串视为不限；非法数字也视为不限 */
function parseBound(s: string): number | null {
  if (s.trim() === "") return null
  const n = Number(s)
  return Number.isFinite(n) ? n : null
}

export function WatchlistPage() {
  const qc = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const [board, setBoard] = useState<"all" | Board>("all")
  const [openMin, setOpenMin] = useState("")
  const [openMax, setOpenMax] = useState("")
  const [hotMin, setHotMin] = useState("")
  const [hotMax, setHotMax] = useState("")

  const importsQ = useQuery({ queryKey: ["watchlistImports"], queryFn: fetchWatchlistImports })
  const imports = importsQ.data ?? []
  // 默认看最近一个交易日；选中的批次被删后回落到最新
  const activeId =
    selected != null && imports.some((i) => i.id === selected) ? selected : imports[0]?.id ?? null
  // imports 按交易日降序：idx+1 是前一个导入日，idx-1 是后一个
  const activeIdx = imports.findIndex((i) => i.id === activeId)
  const activeDate = imports[activeIdx]?.tradeDate ?? ""
  // 手输日期是逐段触发 change 的（输 "28" 先变成 02 号），草稿态里命中导入日就切过去，
  // 失焦时还没命中再提示并复原
  const [dateDraft, setDateDraft] = useState("")
  useEffect(() => setDateDraft(activeDate), [activeDate])
  const changeDate = (d: string) => {
    setDateDraft(d)
    const hit = imports.find((i) => i.tradeDate === d)
    if (hit) setSelected(hit.id)
  }
  const commitDate = () => {
    if (dateDraft && dateDraft !== activeDate) toast.info(`${dateDraft} 没有导入记录`)
    setDateDraft(activeDate)
  }

  // 删除是不可逆的：第一次点击进入确认态，换日期即取消
  const [confirmDel, setConfirmDel] = useState(false)
  useEffect(() => setConfirmDel(false), [activeId])

  const itemsQ = useQuery({
    queryKey: ["watchlistItems", activeId],
    queryFn: () => fetchWatchlistItems(activeId!),
    enabled: activeId != null,
    refetchInterval: 60_000,
    placeholderData: keepPreviousData,
  })

  const uploadM = useMutation({
    mutationFn: async (files: File[]) => {
      // 逐个导入：同一交易日的后一个文件覆盖前一个，顺序要确定
      const results = []
      for (const f of files) results.push(await importWatchlist(f.name, await f.text()))
      return results
    },
    onSuccess: (results) => {
      const last = results[results.length - 1]
      toast.success(
        results.length === 1
          ? `已导入 ${last.tradeDate} 买入 ${last.count} 只${last.replaced ? "（覆盖同日旧导入）" : ""}`
          : `已导入 ${results.length} 个交易日`,
      )
      setSelected(last.id)
      qc.invalidateQueries({ queryKey: ["watchlistImports"] })
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "导入失败"),
    onSettled: () => {
      if (fileRef.current) fileRef.current.value = ""
    },
  })

  const deleteM = useMutation({
    mutationFn: deleteWatchlistImport,
    onSuccess: () => {
      toast.success("已删除")
      qc.invalidateQueries({ queryKey: ["watchlistImports"] })
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "删除失败"),
  })

  const lo = parseBound(openMin)
  const hi = parseBound(openMax)
  const hotLo = parseBound(hotMin)
  const hotHi = parseBound(hotMax)
  const hotActive = hotLo != null || hotHi != null
  const rangeActive = lo != null || hi != null
  const all = itemsQ.data?.items ?? []
  const rows = all.filter((s) => {
    if (board !== "all" && s.board !== board) return false
    if (rangeActive) {
      // 设了开盘区间时，未开盘的股票无从比较，一律不显示
      if (s.openPct == null) return false
      if (lo != null && s.openPct < lo) return false
      if (hi != null && s.openPct > hi) return false
    }
    if (hotActive) {
      // 昨日没有人气排名的（新股 / 未落库）同样无从比较
      if (s.hotRankPrev == null) return false
      if (hotLo != null && s.hotRankPrev < hotLo) return false
      if (hotHi != null && s.hotRankPrev > hotHi) return false
    }
    return true
  })
  const boardCount = (b: "all" | Board) =>
    b === "all" ? all.length : all.filter((s) => s.board === b).length
  const loading = itemsQ.isLoading || (itemsQ.isFetching && itemsQ.isPlaceholderData)

  return (
    <div className="mx-auto max-w-[1600px] space-y-5 px-4 py-6 lg:px-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-foreground">每日精选</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            导入下单文件里标记为买入的股票，跟踪开盘表现与东财人气排名
          </p>
        </div>
        <input
          ref={fileRef}
          type="file"
          accept=".csv,text/csv"
          multiple
          className="hidden"
          onChange={(e) => {
            const files = Array.from(e.target.files ?? [])
            if (files.length) uploadM.mutate(files)
          }}
        />
        <Button onClick={() => fileRef.current?.click()} disabled={uploadM.isPending}>
          {uploadM.isPending ? (
            <CircleNotch className="size-4 animate-spin" />
          ) : (
            <UploadSimple className="size-4" />
          )}
          导入下单文件
        </Button>
      </div>

      <div className="grid grid-cols-1 gap-5">
        <div className="min-w-0 space-y-3">
          <div className="flex flex-wrap items-end gap-4 rounded-lg border border-border p-3">
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">日期</Label>
              <div className="flex items-center gap-1">
                <DayStep
                  label="前一个导入日"
                  disabled={activeIdx < 0 || activeIdx >= imports.length - 1}
                  onClick={() => setSelected(imports[activeIdx + 1].id)}
                >
                  <CaretLeft className="size-3.5" />
                </DayStep>
                <Input
                  type="date"
                  value={dateDraft}
                  min={imports[imports.length - 1]?.tradeDate}
                  max={imports[0]?.tradeDate}
                  disabled={imports.length === 0}
                  onChange={(e) => changeDate(e.target.value)}
                  onBlur={commitDate}
                  className="w-36 font-mono"
                  aria-label="按日期筛选"
                />
                <DayStep
                  label="后一个导入日"
                  disabled={activeIdx <= 0}
                  onClick={() => setSelected(imports[activeIdx - 1].id)}
                >
                  <CaretRight className="size-3.5" />
                </DayStep>
                {activeId != null && (
                  <button
                    onClick={() => {
                      if (!confirmDel) return setConfirmDel(true)
                      deleteM.mutate(activeId)
                      setConfirmDel(false)
                    }}
                    onBlur={() => setConfirmDel(false)}
                    disabled={deleteM.isPending}
                    aria-label={`删除 ${activeDate} 的导入`}
                    title={`删除 ${activeDate} 的导入`}
                    className={cn(
                      "ml-1 flex h-8 items-center gap-1 rounded-md border px-2 text-xs transition-colors disabled:pointer-events-none disabled:opacity-40",
                      confirmDel
                        ? "border-destructive/40 text-destructive"
                        : "border-border text-muted-foreground hover:text-destructive",
                    )}
                  >
                    <Trash className="size-3.5" />
                    {confirmDel && "确认删除"}
                  </button>
                )}
              </div>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">板块</Label>
              <Tabs value={board} onValueChange={(v) => setBoard(v as typeof board)}>
                <TabsList>
                  {BOARDS.map((b) => (
                    <TabsTrigger key={b} value={b}>
                      {b === "all" ? "全部" : b}
                      <span className="ml-1 font-mono text-[11px] text-muted-foreground">
                        {boardCount(b)}
                      </span>
                    </TabsTrigger>
                  ))}
                </TabsList>
              </Tabs>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">开盘涨跌幅 (%)</Label>
              <div className="flex items-center gap-2">
                <Input
                  type="number"
                  step="0.1"
                  inputMode="decimal"
                  placeholder="不限"
                  value={openMin}
                  onChange={(e) => setOpenMin(e.target.value)}
                  className="w-24 font-mono"
                  aria-label="开盘涨跌幅下限"
                />
                <span className="text-muted-foreground">~</span>
                <Input
                  type="number"
                  step="0.1"
                  inputMode="decimal"
                  placeholder="不限"
                  value={openMax}
                  onChange={(e) => setOpenMax(e.target.value)}
                  className="w-24 font-mono"
                  aria-label="开盘涨跌幅上限"
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">昨日人气排名</Label>
              <div className="flex items-center gap-2">
                <Input
                  type="number"
                  step="1"
                  min="1"
                  inputMode="numeric"
                  placeholder="不限"
                  value={hotMin}
                  onChange={(e) => setHotMin(e.target.value)}
                  className="w-24 font-mono"
                  aria-label="昨日人气排名下限"
                />
                <span className="text-muted-foreground">~</span>
                <Input
                  type="number"
                  step="1"
                  min="1"
                  inputMode="numeric"
                  placeholder="不限"
                  value={hotMax}
                  onChange={(e) => setHotMax(e.target.value)}
                  className="w-24 font-mono"
                  aria-label="昨日人气排名上限"
                />
              </div>
            </div>
            {(board !== "all" || rangeActive || hotActive) && (
              <button
                onClick={() => {
                  setBoard("all")
                  setOpenMin("")
                  setOpenMax("")
                  setHotMin("")
                  setHotMax("")
                }}
                className="flex h-8 items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
              >
                <ArrowCounterClockwise className="size-3" />
                重置
              </button>
            )}
          </div>

          {activeId != null && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              {itemsQ.data?.tradeDate} 共{" "}
              <span className="font-mono font-medium text-foreground">{rows.length}</span>
              {rows.length !== all.length && <> / {all.length}</>} 只
              {loading && <CircleNotch className="size-3.5 animate-spin" />}
              <button
                onClick={() => itemsQ.refetch()}
                disabled={itemsQ.isFetching}
                aria-label="刷新"
                title="刷新行情与人气排名"
                className="flex size-6 items-center justify-center rounded text-muted-foreground transition-colors hover:text-foreground disabled:pointer-events-none"
              >
                <ArrowsClockwise className={cn("size-3.5", itemsQ.isFetching && "animate-spin")} />
              </button>
            </p>
          )}

          {rows.length > 0 && (
            <div
              className={cn(
                "rounded-lg border border-border transition-opacity",
                loading && "pointer-events-none opacity-50",
              )}
            >
              <QuoteTable
                key={activeId}
                data={rows}
                extraColumns={EXTRA_COLUMNS}
                defaultSorting={[{ id: "hotRank", desc: false }]}
                pageSize={100}
              />
            </div>
          )}
          {rows.length === 0 && loading && (
            <div className="flex items-center justify-center gap-2 rounded-lg border border-dashed border-border py-14">
              <CircleNotch className="size-4 animate-spin text-muted-foreground" />
              <p className="text-sm text-muted-foreground">加载中…</p>
            </div>
          )}
          {rows.length === 0 && !loading && (
            <div className="rounded-lg border border-dashed border-border py-14 text-center">
              <p className="text-sm text-muted-foreground">
                {importsQ.isLoading
                  ? "加载中…"
                  : activeId == null
                    ? "还没有导入。点右上角导入 kf_order_*.csv 下单文件，可一次选多个"
                    : "没有符合条件的股票，试着放宽筛选"}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
