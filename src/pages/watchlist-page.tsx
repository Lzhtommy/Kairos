import { useLiveQuotes } from "@/lib/use-live-quotes"
import { QuoteTable } from "@/components/market/quote-table"

export function WatchlistPage() {
  const { stocks } = useLiveQuotes()
  const watched = stocks.slice(0, 6)

  return (
    <div className="mx-auto max-w-[1600px] space-y-5 px-4 py-6 lg:px-6">
      <div>
        <h1 className="text-lg font-semibold text-foreground">自选股</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          点击行情表中的星标即可添加或移除自选
        </p>
      </div>
      {watched.length > 0 ? (
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
