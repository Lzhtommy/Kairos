import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { MagnifyingGlass } from "@phosphor-icons/react"
import { useLiveQuotes } from "@/lib/use-live-quotes"
import { fetchRanking } from "@/api/market"
import { fetchWatchlist } from "@/api/watchlist"
import { IndexStrip } from "@/components/market/index-strip"
import { QuoteTable } from "@/components/market/quote-table"
import { Input } from "@/components/ui/input"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"

type Tab = "all" | "watchlist" | "gainers" | "active"

export function DashboardPage() {
  const { stocks, indices } = useLiveQuotes()
  const [tab, setTab] = useState<Tab>("all")
  const [query, setQuery] = useState("")

  const gainersQ = useQuery({
    queryKey: ["ranking", "gainers"],
    queryFn: () => fetchRanking("gainers"),
    enabled: tab === "gainers",
    refetchInterval: 60_000,
  })
  const activeQ = useQuery({
    queryKey: ["ranking", "active"],
    queryFn: () => fetchRanking("active"),
    enabled: tab === "active",
    refetchInterval: 60_000,
  })
  const watchQ = useQuery({
    queryKey: ["watchlist"],
    queryFn: fetchWatchlist,
    enabled: tab === "watchlist",
  })

  const data =
    tab === "gainers"
      ? gainersQ.data ?? []
      : tab === "active"
        ? activeQ.data ?? []
        : tab === "watchlist"
          ? watchQ.data?.items ?? []
          : stocks

  const q = query.trim().toLowerCase()
  const filtered = q
    ? data.filter((s) => s.code.includes(q) || s.name.toLowerCase().includes(q))
    : data

  return (
    <div className="mx-auto max-w-[1600px] space-y-6 px-4 py-6 lg:px-6">
      <div>
        <h1 className="text-lg font-semibold text-foreground">大盘概览</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          实时行情约每分钟刷新一次
        </p>
      </div>

      <IndexStrip indices={indices} />

      <div>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
            <TabsList>
              <TabsTrigger value="all">全部 A 股</TabsTrigger>
              <TabsTrigger value="watchlist">自选股</TabsTrigger>
              <TabsTrigger value="gainers">涨幅榜</TabsTrigger>
              <TabsTrigger value="active">成交活跃</TabsTrigger>
            </TabsList>
          </Tabs>
          <div className="relative w-full max-w-60">
            <MagnifyingGlass className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索代码 / 名称"
              className="h-8 border-transparent bg-muted/60 pl-8 focus-visible:bg-background"
            />
          </div>
        </div>
        {filtered.length > 0 ? (
          <div className="rounded-lg border border-border">
            {/* key 让换 tab / 改搜索词时分页回到第一页 */}
            <QuoteTable key={`${tab}-${q}`} data={filtered} />
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-border py-14 text-center">
            <p className="text-sm text-muted-foreground">
              {q
                ? `没有匹配「${query.trim()}」的股票`
                : tab === "watchlist"
                  ? "还没有自选股，点击星标添加"
                  : "加载中…"}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
