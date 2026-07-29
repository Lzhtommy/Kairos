import { useQuery } from "@tanstack/react-query"
import { fetchQuotes, fetchIndices, fetchRanking } from "@/api/market"
import type { Stock } from "@/lib/mock-data"

// Low-frequency polling (~1 min) — matches the MVP data cadence.
const POLL_MS = 60_000

export function useLiveQuotes(intervalMs = POLL_MS) {
  const quotesQ = useQuery({
    queryKey: ["quotes"],
    queryFn: () => fetchQuotes(),
    refetchInterval: intervalMs,
    staleTime: intervalMs,
  })
  const indicesQ = useQuery({
    queryKey: ["indices"],
    queryFn: () => fetchIndices(),
    refetchInterval: intervalMs,
    staleTime: intervalMs,
  })

  return {
    stocks: quotesQ.data ?? [],
    indices: indicesQ.data ?? [],
    isLoading: quotesQ.isLoading || indicesQ.isLoading,
  }
}

/** 营销页展示用的轻量行情：只拉成交活跃榜前 N 只 + 指数，避免全市场大 payload。 */
export function useShowcaseQuotes(count = 6) {
  const rankingQ = useQuery({
    queryKey: ["ranking", "active", count],
    queryFn: () => fetchRanking("active", count),
    refetchInterval: POLL_MS,
    staleTime: POLL_MS,
  })
  const indicesQ = useQuery({
    queryKey: ["indices"],
    queryFn: () => fetchIndices(),
    refetchInterval: POLL_MS,
    staleTime: POLL_MS,
  })
  return {
    stocks: rankingQ.data ?? [],
    indices: indicesQ.data ?? [],
    isLoading: rankingQ.isLoading || indicesQ.isLoading,
  }
}

export function changePct(stock: Stock) {
  return ((stock.price - stock.prevClose) / stock.prevClose) * 100
}
