import { api } from "@/api/client"

/** 回测参数——与后端 BacktestIn 对应，数值范围在服务端夹紧。 */
export type BacktestParams = {
  periodDays?: number // 120≈半年 250≈1年 500≈2年 750≈3年
  holdDays?: number
  entry?: "open" | "close"
  exitRule?: "hold" | "signal" | "stop"
  stopGain?: number
  stopLoss?: number
  rebalance?: "weekly" | "monthly" | "quarterly"
  costRate?: number
  benchmark?: "000300" | "000905" | "399006"
  weighting?: "equal" | "cap"
  maxPositions?: number
}

/** 指标按模式区分：event（事件驱动）与 portfolio（组合持有）字段不同。 */
export type BacktestMetrics = {
  mode?: "event" | "portfolio"
  hitCount?: number
  benchmarkName?: string
  // portfolio
  annualizedReturn?: number
  maxDrawdown?: number
  sharpe?: number
  winRate?: number
  totalReturn?: number
  benchmarkReturn?: number | null
  excessReturn?: number | null
  rebalances?: number
  pitPeriods?: number // 用真实快照重选的期数（随每日归档积累增长）
  // event
  eventCount?: number
  avgReturn?: number
  medianReturn?: number
  avgHoldDays?: number
  avgExcess?: number | null
}

export type BacktestResult = {
  id: number
  status: "pending" | "running" | "done" | "failed"
  metrics: BacktestMetrics
  curve: { t: string; v: number }[]
  benchmark: { t: string; v: number }[]
  error: string | null
}

export function submitBacktest(
  strategyId: string,
  params: BacktestParams = {},
): Promise<{ id: number; status: string }> {
  return api("/backtests", {
    method: "POST",
    body: { strategyId: Number(strategyId), ...params },
  })
}

export function fetchBacktest(id: number): Promise<BacktestResult> {
  return api<BacktestResult>(`/backtests/${id}`)
}

/** 历史回测摘要（列表项） */
export type BacktestSummary = {
  id: number
  status: BacktestResult["status"]
  params: BacktestParams
  metrics: BacktestMetrics
  createdAt: string
}

export function fetchBacktests(strategyId: string): Promise<BacktestSummary[]> {
  return api<BacktestSummary[]>(`/backtests?strategyId=${Number(strategyId)}`)
}

export function deleteBacktest(id: number): Promise<unknown> {
  return api(`/backtests/${id}`, { method: "DELETE" })
}

/** 事件模式的逐笔交易 */
export type BacktestTrade = {
  code: string
  name: string
  entryDate: string
  entryPx: number
  exitDate: string
  exitPx: number
  ret: number // %
  days: number
  reason: "hold" | "signal" | "stop_gain" | "stop_loss"
}

/** 组合模式的调仓记录 */
export type RebalanceRecord = {
  date: string
  holdings: number
  addedCount: number
  removedCount: number
  added: { code: string; name: string }[]
  removed: { code: string; name: string }[]
  periodReturn: number // %
}

export type TradesPage = {
  kind: "trades" | "rebalances"
  total: number
  page: number
  pageSize: number
  items: BacktestTrade[] | RebalanceRecord[]
}

export function fetchBacktestTrades(
  id: number,
  page = 1,
  sort: "date" | "ret" = "date",
  order: "asc" | "desc" = "desc",
): Promise<TradesPage> {
  return api<TradesPage>(
    `/backtests/${id}/trades?page=${page}&pageSize=50&sort=${sort}&order=${order}`,
  )
}
