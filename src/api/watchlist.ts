import { api } from "@/api/client"
import type { OpenHotStock } from "@/components/market/open-hot-columns"

export type Board = "主板" | "创业板" | "科创板" | "北交所"

export type WatchlistImport = {
  id: number
  tradeDate: string
  filename: string
  count: number
  createdAt: string | null
}

export type WatchlistStock = OpenHotStock & {
  board: Board
  /** 下单文件里的买入数量（股） */
  quantity: number
}

export function fetchWatchlistImports(): Promise<WatchlistImport[]> {
  return api<WatchlistImport[]>("/watchlist/imports")
}

export function importWatchlist(
  filename: string,
  content: string,
): Promise<WatchlistImport & { replaced: boolean }> {
  return api("/watchlist/imports", { method: "POST", body: { filename, content } })
}

export function deleteWatchlistImport(id: number): Promise<void> {
  return api<void>(`/watchlist/imports/${id}`, { method: "DELETE" })
}

export function fetchWatchlistItems(
  id: number,
): Promise<WatchlistImport & { prevDate: string; items: WatchlistStock[] }> {
  return api(`/watchlist/imports/${id}/items`)
}
