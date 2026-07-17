import { api } from "@/api/client"

export type BacktestMetrics = {
  annualizedReturn: number
  maxDrawdown: number
  sharpe: number
  winRate: number
  hitCount: number
}

export type BacktestResult = {
  id: number
  status: "pending" | "running" | "done" | "failed"
  metrics: BacktestMetrics | Record<string, never>
  curve: { t: string; v: number }[]
  error: string | null
}

export function submitBacktest(strategyId: string): Promise<{ id: number; status: string }> {
  return api("/backtests", { method: "POST", body: { strategyId: Number(strategyId) } })
}

export function fetchBacktest(id: number): Promise<BacktestResult> {
  return api<BacktestResult>(`/backtests/${id}`)
}
