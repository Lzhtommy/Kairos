import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { useLiveQuotes } from "@/lib/use-live-quotes"
import { fetchRanking } from "@/api/market"
import { fetchWatchlist } from "@/api/watchlist"
import { IndexStrip } from "@/components/market/index-strip"
import { QuoteTable } from "@/components/market/quote-table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"

type Tab = "all" | "watchlist" | "gainers" | "active"

export function DashboardPage() {
  const { stocks, indices } = useLiveQuotes()
  const [tab, setTab] = useState<Tab>("all")

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
        <div className="mb-3 flex items-center justify-between">
          <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
            <TabsList>
              <TabsTrigger value="all">全部 A 股</TabsTrigger>
              <TabsTrigger value="watchlist">自选股</TabsTrigger>
              <TabsTrigger value="gainers">涨幅榜</TabsTrigger>
              <TabsTrigger value="active">成交活跃</TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
        {data.length > 0 ? (
          <div className="rounded-lg border border-border">
            {/* key 让换 tab 时分页/排序回到初始状态 */}
            <QuoteTable key={tab} data={data} />
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-border py-14 text-center">
            <p className="text-sm text-muted-foreground">
              {tab === "watchlist" ? "还没有自选股，点击星标添加" : "加载中…"}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
