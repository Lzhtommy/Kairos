import { api } from "@/api/client"

/** 模拟盘参数——与回测事件模式同名同义，服务端夹紧。 */
export type PaperParams = {
  holdDays?: number
  exitRule?: "hold" | "signal" | "stop"
  stopGain?: number
  stopLoss?: number
  maxConcurrent?: number
  costRate?: number
}

export type PaperSummary = {
  strategyId: string
  strategyName: string
  enabled: boolean
  equity: number | null
  lastSettled: string | null
}

export type PaperPosition = {
  code: string
  name: string
  status: "pending" | "open"
  signalDate: string
  entryDate: string | null
  entryPx: number
  lastPrice: number
  holdDays: number
  /** 持仓浮动收益%（pending 为 null） */
  ret: number | null
}

export type PaperTrade = {
  code: string
  name: string
  entryDate: string
  entryPx: number
  exitDate: string
  exitPx: number
  ret: number
  reason: "hold" | "signal" | "stop_gain" | "stop_loss"
}

export type PaperAccount = {
  strategyId: string
  strategyName: string
  enabled: boolean
  params: Required<PaperParams>
  equity: number
  stats: { skippedByLimit?: number; skippedByCapacity?: number }
  startedAt: string | null
  curve: { t: string; v: number }[]
  /** 最近一次完成回测的同段归一曲线（与模拟盘起点对齐到 1.0），无重叠时为 null */
  backtestOverlay: {
    backtestId: number
    createdAt: string
    params: Record<string, unknown>
    curve: { t: string; v: number }[]
  } | null
  positions: PaperPosition[]
  trades: PaperTrade[]
  tradeCount: number
  winRate: number | null
}

export function fetchPaperAccounts(): Promise<PaperSummary[]> {
  return api<PaperSummary[]>("/paper")
}

export function fetchPaperAccount(strategyId: string): Promise<PaperAccount> {
  return api<PaperAccount>(`/paper/${strategyId}`)
}

export function togglePaper(
  strategyId: string,
  enabled: boolean,
  params?: PaperParams,
): Promise<{ enabled: boolean }> {
  return api(`/paper/${strategyId}`, { method: "POST", body: { enabled, params } })
}

export function resetPaper(strategyId: string): Promise<unknown> {
  return api(`/paper/${strategyId}`, { method: "DELETE" })
}
