import { useQuery } from "@tanstack/react-query"
import { fetchQuotes, fetchIndices } from "@/api/market"
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

export function changePct(stock: Stock) {
  return ((stock.price - stock.prevClose) / stock.prevClose) * 100
}
