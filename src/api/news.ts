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
  /** 0 普通 / 1 重要（源标记重要或 3 家以上同报）/ 2 特别重要 */
  level: number
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

export function fetchNewsEvents(params: {
  before?: number
  important?: boolean
  q?: string
  limit?: number
}): Promise<NewsPage> {
  const sp = new URLSearchParams()
  if (params.before != null) sp.set("before", String(params.before))
  if (params.important) sp.set("important", "true")
  if (params.q) sp.set("q", params.q)
  if (params.limit) sp.set("limit", String(params.limit))
  const qs = sp.toString()
  return api<NewsPage>(`/news/events${qs ? `?${qs}` : ""}`)
}

export function fetchNewsSources(): Promise<NewsSourceStatus[]> {
  return api<NewsSourceStatus[]>("/news/sources")
}
