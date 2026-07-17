import { api } from "@/api/client"
import type { Stock } from "@/lib/mock-data"

export type WatchlistResp = { codes: string[]; items: Stock[] }

export function fetchWatchlist(): Promise<WatchlistResp> {
  return api<WatchlistResp>("/watchlist")
}

export function addToWatchlist(code: string): Promise<unknown> {
  return api(`/watchlist/${code}`, { method: "POST" })
}

export function removeFromWatchlist(code: string): Promise<unknown> {
  return api(`/watchlist/${code}`, { method: "DELETE" })
}
