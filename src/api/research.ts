import { api, getToken, API_BASE_URL } from "@/api/client"

export type Grade = "A" | "B" | "C" | "D"

export const DIM_ORDER = [
  "profitability",
  "growth",
  "health",
  "cashflow",
  "valuation",
  "shareholder",
  "events",
] as const

export type DimSummary = { label: string; score: number | null; max: number }

export type ResearchSummary = {
  id: number
  code: string
  name: string
  industry: string
  grade: Grade | null
  total_score: number | null
  coverage: number | null
  dims: Record<string, DimSummary>
  red_flags: number
  data_as_of: string | null
  scoring_version: string
  finished_at: string | null
  updating: boolean
}

export type ScoreItem = {
  key: string
  label: string
  value: number | null
  display: string
  unit?: string | null
  period?: string | null
  source?: string | null
  points: number | null
  max: number | null
  zero: number | null
  full: number | null
  note?: string | null
}

export type SubScore = DimSummary & {
  available: number
  insufficient: boolean
  items: ScoreItem[]
}

export type Fact = {
  value: number
  unit: string
  label: string
  source: string
  period: string | null
  group: string
}

export type RedFlag = { key: string; label: string; detail: string }

export type EventItem = { date: string | null; title: string; url: string | null; tag: string; label: string }
export type NewsItem = { title: string; time: string; source: string; url: string | null }
export type InstReport = { date: string | null; org: string; title: string; rating: string; url: string | null }
/** 年报深读条目：quote 为年报原文，page 由程序按原文回查 */
export type AnnualKpi = { name: string; value: string; unit: string; quote: string; page: number }
export type AnnualRisk = { title: string; quote: string; page: number }
export type AnnualCitation = { topic: string; claim: string; quote: string; page: number }

export type Section = {
  key: string
  title: string
  md: string
  metric_keys: string[]
  ai: boolean
  warning?: string | null
  observations?: string[]
  items?: (EventItem | NewsItem)[]
  reports?: InstReport[]
  profile?: Record<string, string>
  segments?: { name: string; share_fact: string; gm_fact: string }[]
  kpis?: AnnualKpi[]
  company_risks?: AnnualRisk[]
  citations?: AnnualCitation[]
}

export type SourceInfo = {
  key: string
  label: string
  fetched_at: string | null
  status: "ok" | "cache" | "stale" | "failed"
  error?: string
  url?: string
}

export type ResearchDetail = ResearchSummary & {
  status: string
  sub_scores: Record<string, SubScore>
  red_flags: RedFlag[]
  sections: Section[]
  facts: Record<string, Fact>
  sources: SourceInfo[]
  llm_model: string | null
  created_at: string | null
}

export type HistoryPoint = {
  id: number
  total_score: number | null
  grade: Grade | null
  data_as_of: string | null
  scoring_version: string
  finished_at: string | null
}

export type ResearchTask = {
  id: number
  code: string
  name: string
  status: "queued" | "running" | "done" | "failed"
  progress: string
  error: string | null
  grade: Grade | null
  total_score: number | null
  created_at: string | null
  finished_at: string | null
}

export type GenerateResult = { code: string; name?: string; ok: boolean; id?: number; reason?: string }

export const fetchResearchList = () => api<ResearchSummary[]>("/research")
export const fetchResearch = (id: number) => api<ResearchDetail>(`/research/${id}`)
export const fetchResearchHistory = (code: string) =>
  api<HistoryPoint[]>(`/research/stock/${code}/history`)

export async function downloadResearchMarkdown(id: number, filename: string) {
  const res = await fetch(`${API_BASE_URL}/research/${id}/markdown`, {
    headers: { Authorization: `Bearer ${getToken() ?? ""}` },
  })
  if (!res.ok) throw new Error(`下载失败 (${res.status})`)
  const url = URL.createObjectURL(new Blob([await res.text()], { type: "text/markdown;charset=utf-8" }))
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export const generateResearch = (codes: string[], force: boolean) =>
  api<GenerateResult[]>("/admin/research", { method: "POST", body: { codes, force } })
export const fetchResearchTasks = () => api<ResearchTask[]>("/admin/research/tasks")
export const retryResearch = (id: number) =>
  api<ResearchTask>(`/admin/research/${id}/retry`, { method: "POST" })
export const deleteResearch = (id: number) => api(`/admin/research/${id}`, { method: "DELETE" })
