import { api } from "@/api/client"
import type { Stock } from "@/lib/mock-data"

export type Board = "主板" | "创业板" | "科创板" | "北交所"

export type WatchlistImport = {
  id: number
  tradeDate: string
  filename: string
  count: number
  createdAt: string | null
}

export type WatchlistStock = Stock & {
  board: Board
  /** 下单文件里的买入数量（股） */
  quantity: number
  /** 交易日开盘价相对前收的涨跌幅%；未开盘为 null */
  openPct: number | null
  /** 交易日东财人气排名：交易日是今天时为实时排名，过去的交易日为当天的日排名 */
  hotRank: number | null
  /** 交易日前一自然日的人气排名（落库的日排名） */
  hotRankPrev: number | null
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
