import { api } from "@/api/client"
import type { Stock, IndexQuote } from "@/lib/mock-data"

export function fetchQuotes(codes?: string[]): Promise<Stock[]> {
  const q = codes && codes.length ? `?codes=${codes.join(",")}` : ""
  return api<Stock[]>(`/quotes${q}`)
}

export function fetchIndices(): Promise<IndexQuote[]> {
  return api<IndexQuote[]>("/indices")
}

export function fetchRanking(type: "gainers" | "active", limit = 30): Promise<Stock[]> {
  return api<Stock[]>(`/quotes/ranking?type=${type}&limit=${limit}`)
}

export type ScreenerParams = {
  industry?: string
  peMin?: number
  peMax?: number
  roeMin?: number
  page?: number
  pageSize?: number
}

export type ScreenerResult = {
  total: number
  page: number
  pageSize: number
  items: Stock[]
}

export function runScreener(params: ScreenerParams): Promise<ScreenerResult> {
  return api<ScreenerResult>("/screener/run", { method: "POST", body: params })
}
