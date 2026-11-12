import { api } from "@/api/client"

export type SessionKind = "premarket" | "postclose" | "review" | "manual"

export type AgentSessionRow = {
  id: number
  kind: SessionKind
  kindLabel: string
  tradeDate: string
  status: "running" | "done" | "failed"
  preview: string
  steps: number
  tokens: number
  model: string | null
  error: string | null
  startedAt: string | null
  finishedAt: string | null
}

export type AgentSummary = {
  id: number
  name: string
  enabled: boolean
  initialCash: number
  total: number
  returnPct: number
  benchmark: string
  benchReturnPct: number | null
  lastSettled: string | null
  playbookVersion: number
  lastSession: AgentSessionRow | null
  createdAt: string | null
}

export type AgentOrder = {
  id: number
  code: string
  name: string
  side: "buy" | "sell"
  shares: number
  limitPrice: number | null
  status: "pending" | "filled" | "expired" | "rejected" | "cancelled"
  tradeDate: string | null
  filledShares: number
  fillPrice: number | null
  amount: number
  fee: number
  realizedPnl: number | null
  note: string
  reason: string
  memoryIds: number[]
  sessionId: number | null
  createdAt: string | null
}

export type AgentHolding = {
  code: string
  name: string
  shares: number
  avgCost: number
  price: number
  value: number
  pnl: number
  pnlPct: number | null
  openedAt: string
}

export type AgentMemory = {
  id: number
  kind: "lesson" | "rule" | "watch"
  content: string
  tags: string[]
  created: string | null
  active: boolean
  retireReason: string
  outcome: { orders: number; avgRetPct: number; wins: number } | null
}

type Window = {
  from: string
  to: string
  returnPct: number
  benchPct: number | null
  maxDrawdownPct: number
  fees: number
} | null

export type AgentDetail = AgentSummary & {
  portfolio: {
    cash: number
    availableCash: number
    marketValue: number
    total: number
    positions: AgentHolding[]
  }
  curve: { t: string; v: number }[]
  benchCurve: { t: string; v: number }[]
  stats: {
    sinceInception: Window
    recent: Window
    closedTrades: number
    winRate: number | null
    totalFees: number
  }
  fees: Record<string, number>
  playbook: string
  orders: AgentOrder[]
  memories: AgentMemory[]
  running: boolean
}

export type AgentStep = { tool: string; args: Record<string, unknown>; result: string; at: string }

export type AgentSessionDetail = AgentSessionRow & {
  summary: string
  trace: AgentStep[]
  orders: AgentOrder[]
}

export type Playbook = {
  version: number
  content: string
  rationale: string
  sessionId: number | null
  createdAt: string | null
}

export function fetchAgentAccounts() {
  return api<{ llmEnabled: boolean; model: string | null; items: AgentSummary[] }>("/agent/accounts")
}

export function fetchAgentAccount(id: number) {
  return api<AgentDetail>(`/agent/accounts/${id}`)
}

export function fetchAgentSessions(id: number) {
  return api<AgentSessionRow[]>(`/agent/accounts/${id}/sessions`)
}

export function fetchAgentSession(id: number) {
  return api<AgentSessionDetail>(`/agent/sessions/${id}`)
}

export function fetchPlaybooks(id: number) {
  return api<Playbook[]>(`/agent/accounts/${id}/playbooks`)
}

export function createAgentAccount(body: { name: string; initialCash: number }) {
  return api<AgentSummary>("/admin/agent/accounts", { method: "POST", body })
}

export function patchAgentAccount(id: number, body: { name?: string; enabled?: boolean }) {
  return api<AgentSummary>(`/admin/agent/accounts/${id}`, { method: "PATCH", body })
}

export function deleteAgentAccount(id: number) {
  return api<void>(`/admin/agent/accounts/${id}`, { method: "DELETE" })
}

export function runAgent(id: number, kind: SessionKind) {
  return api<{ ok: boolean }>(`/admin/agent/accounts/${id}/run`, { method: "POST", body: { kind } })
}
