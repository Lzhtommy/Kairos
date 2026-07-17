import { useQuery } from "@tanstack/react-query"
import { QuoteTable } from "@/components/market/quote-table"
import { fetchWatchlist } from "@/api/watchlist"

export function WatchlistPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["watchlist"],
    queryFn: fetchWatchlist,
    refetchInterval: 60_000,
  })
  const watched = data?.items ?? []

  return (
    <div className="mx-auto max-w-[1600px] space-y-5 px-4 py-6 lg:px-6">
      <div>
        <h1 className="text-lg font-semibold text-foreground">自选股</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          点击行情表中的星标即可添加或移除自选
        </p>
      </div>
      {isLoading ? (
        <div className="rounded-lg border border-dashed border-border py-16 text-center">
          <p className="text-sm text-muted-foreground">加载中…</p>
        </div>
      ) : watched.length > 0 ? (
        <div className="rounded-lg border border-border">
          <QuoteTable data={watched} />
        </div>
      ) : (
        <div className="rounded-lg border border-dashed border-border py-16 text-center">
          <p className="text-sm text-muted-foreground">还没有自选股，去大盘页添加一些吧</p>
        </div>
      )}
    </div>
  )
}
