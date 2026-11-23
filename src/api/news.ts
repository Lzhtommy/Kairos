import { api } from "@/api/client"

export type NewsSourceRef = {
  source: string
  name: string
  url: string | null
  pub_at: string
  importance: number
}

export type NewsEvent = {
  id: number
  pub_at: string
  /** 要闻：LLM 重要度 ≥ 4；还没打标时按源标记 + 报道家数兜底 */
  featured: boolean
  /** LLM 重要度 1-5，未打标为 null */
  score: number | null
  category: string | null
  sectors: string[]
  stocks: { code: string; name: string }[]
  title: string
  content: string
  sources: NewsSourceRef[]
  /** 同一件事的后续（同一讲话的其他表态、行情进一步变化、他家报道），按时间正序 */
  followups: NewsFollowup[]
  /** 最后一条后续的时间，没有后续时等于 pub_at */
  last_at: string
}

export type NewsFollowup = {
  id: number
  pub_at: string
  title: string
  content: string
  sources: NewsSourceRef[]
}

export type NewsPage = { events: NewsEvent[]; next: number | null }

export type NewsSourceStatus = {
  source: string
  name: string
  last_seen: string | null
  count_24h: number
  failing: boolean
}

export type NewsView = "featured" | "all"

export function fetchNewsEvents(params: {
  before?: number
  view: NewsView
  category?: string
  q?: string
  limit?: number
}): Promise<NewsPage> {
  const sp = new URLSearchParams({ view: params.view })
  if (params.before != null) sp.set("before", String(params.before))
  if (params.category) sp.set("category", params.category)
  if (params.q) sp.set("q", params.q)
  if (params.limit) sp.set("limit", String(params.limit))
  const qs = sp.toString()
  return api<NewsPage>(`/news/events${qs ? `?${qs}` : ""}`)
}

export function fetchNewsSources(): Promise<NewsSourceStatus[]> {
  return api<NewsSourceStatus[]>("/news/sources")
}

export function fetchNewsCategories(): Promise<string[]> {
  return api<string[]>("/news/categories")
}
