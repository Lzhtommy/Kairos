import { api } from "@/api/client"

export type SectorRRG = {
  code: string
  trail: [number, number][]
  rel4w: number | null
}

export type SectorCrowd = {
  crowd: number | null
  share: number | null
  heat: number | null
  bias: number | null
}

export type SectorTiltInfo = {
  score: number | null
  rank: number | null
  suggestion: string
  rev1_z: number | null
  season_z: number | null
  resmom_z: number | null
}

export type NarrativeItem = {
  date?: string
  code?: string
  name?: string
  direction: number | null
  strength: number | null
  summary: string
  evidence: string[]
}

export type SectorRow = {
  code: string
  name: string
  close: number | null
  chg_pct: number | null
  rrg: SectorRRG | null
  crowd: SectorCrowd | null
  tilt: SectorTiltInfo | null
  narrative: NarrativeItem | null
}

export type SectorOverview = {
  source: string | null
  asof: string | null
  metric_date: string | null
  tilt_month: string | null
  market_narrative: NarrativeItem | null
  sectors: SectorRow[]
}

export type NarrativeDay = {
  date: string
  market: NarrativeItem | null
  items: NarrativeItem[]
}

export function fetchSectorOverview(): Promise<SectorOverview> {
  return api<SectorOverview>("/sector/overview")
}

export function fetchSectorNarrative(days = 14): Promise<NarrativeDay[]> {
  return api<NarrativeDay[]>(`/sector/narrative?days=${days}`)
}
