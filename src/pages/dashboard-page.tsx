import { useLiveQuotes } from "@/lib/use-live-quotes"
import { IndexStrip } from "@/components/market/index-strip"
import { QuoteTable } from "@/components/market/quote-table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"

export function DashboardPage() {
  const { stocks, indices } = useLiveQuotes()

  return (
    <div className="mx-auto max-w-[1600px] space-y-6 px-4 py-6 lg:px-6">
      <div>
        <h1 className="text-lg font-semibold text-foreground">大盘概览</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          实时行情每 2 秒刷新一次，模拟数据仅供演示
        </p>
      </div>

      <IndexStrip indices={indices} />

      <div>
        <div className="mb-3 flex items-center justify-between">
          <Tabs defaultValue="all">
            <TabsList>
              <TabsTrigger value="all">全部 A 股</TabsTrigger>
              <TabsTrigger value="watchlist">自选股</TabsTrigger>
              <TabsTrigger value="gainers">涨幅榜</TabsTrigger>
              <TabsTrigger value="active">成交活跃</TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
        <div className="rounded-lg border border-border">
          <QuoteTable data={stocks} />
        </div>
      </div>
    </div>
  )
}
