import { api } from "@/api/client"

export type StockHit = {
  code: string
  name: string
  market: string
  industry: string
  delistDate: string | null
}

export type FinRow = {
  reportDate: string
  noticeDate: string | null
  eps: number | null
  deductEps: number | null
  revenue: number | null
  netProfit: number | null
  revenueYoy: number | null
  profitYoy: number | null
  roe: number | null
  bps: number | null
  ocfPs: number | null
  grossMargin: number | null
  dividendPlan: string | null
}

export type DividendRow = {
  reportDate: string
  planNoticeDate: string | null
  recordDate: string | null
  exDate: string | null
  cashPer10: number | null
  bonusPer10: number | null
  transferPer10: number | null
  progress: string
  plan: string
}

export type CapitalRow = {
  date: string
  noticeDate: string | null
  total: number | null
  floatA: number | null
  limited: number | null
  reason: string
}

export type HolderRow = {
  rank: number
  name: string
  type: string
  sharesType: string
  holdNum: number | null
  ratio: number | null
  change: string
}

export type HolderTable = { endDate: string | null; noticeDate: string | null; items: HolderRow[] }

export type LockupRow = {
  date: string
  type: string
  shares: number | null
  ableShares: number | null
  marketCap: number | null
  floatRatio: number | null
  totalRatio: number | null
  upcoming: boolean
}

export type StockProfile = {
  info: {
    code: string
    name: string
    market: string
    industry: string
    listDate: string | null
    delistDate: string | null
  }
  quote: {
    price: number
    changePct: number
    pe: number | null
    pb: number | null
    marketCap: number | null
    turnoverRate: number | null
    dividendYield: number | null
    ts: string | null
  } | null
  kline: { first: string | null; last: string | null; count: number }
  indexes: { index: string; name: string; current: boolean; first: string; last: string }[]
  financials: FinRow[]
  dividends: DividendRow[]
  statements: {
    periods: string[]
    noticeDates: (string | null)[]
    items: { key: string; label: string; statement: "bs" | "is" | "cf"; values: (number | null)[] }[]
  }
  capital: CapitalRow[]
  holderCount: { date: string; num: number; avgShares: number | null }[]
  topHolders: { all: HolderTable; float: HolderTable }
  lockups: LockupRow[]
}

export type AnnouncementItem = {
  artCode: string
  title: string
  category: string
  date: string
  summary: string | null
}

export type AnnouncementPage = {
  page: number
  pageSize: number
  total: number | null
  items: AnnouncementItem[]
}

export type MacroCatalogItem = {
  key: string
  name: string
  unit: string
  freq: "D" | "M" | "Q"
  period: string | null
  value: number | null
  prev: number | null
}

export type MacroSeries = {
  key: string
  name: string
  unit: string
  freq: string
  points: [string, number][]
}

export type Bar = { t: string; open: number; high: number; low: number; close: number; volume: number }

export const searchStocks = (q: string) => api<StockHit[]>(`/data/search?q=${encodeURIComponent(q)}`)
export const fetchStockProfile = (code: string) => api<StockProfile>(`/data/stock/${code}`)
export const fetchAnnouncements = (code: string, page: number) =>
  api<AnnouncementPage>(`/data/stock/${code}/announcements?page=${page}`)
export const summarizeAnnouncement = (artCode: string) =>
  api<AnnouncementItem>(`/data/announcements/${artCode}/summary`, { method: "POST" })
export const fetchMacroCatalog = () => api<MacroCatalogItem[]>("/data/macro")
export const fetchMacroSeries = (key: string, years: number) =>
  api<MacroSeries>(`/data/macro/${key}?years=${years}`)
export const fetchKline = (code: string, limit: number) => api<Bar[]>(`/kline?code=${code}&limit=${limit}`)
