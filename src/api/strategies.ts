import { api, API_BASE_URL, getToken } from "@/api/client"
import type { Stock } from "@/lib/mock-data"

/** 单档前瞻收益：签数 / 等权平均收益% / 胜率%（信号次日开盘入场，D+N 收盘结算） */
export type ForwardStat = { n: number; avg: number; win: number }
export type ForwardStats = { d1?: ForwardStat; d5?: ForwardStat; d10?: ForwardStat }

export type StrategyRunSummary = {
  date: string
  hitCount: number
  addedCount: number
  removedCount: number
  added: { code: string; name: string }[]
  removed: { code: string; name: string }[]
  /** 信号前瞻收益，未成熟的档位缺省 */
  forward?: ForwardStats
}

export type Strategy = {
  id: string
  name: string
  description: string
  tags: string[]
  dsl: Record<string, unknown>
  code: string
  hitCount: number
  createdAt: string
  lastRun?: StrategyRunSummary | null
  hitTrend?: number[]
  /** 近 5 次盘后信号的样本外收益合并（按签数加权），无成熟数据时为 null */
  signalStats?: ForwardStats | null
}

export function fetchStrategies(): Promise<Strategy[]> {
  return api<Strategy[]>("/strategies")
}

export function createStrategy(body: {
  name: string
  description?: string
  tags?: string[]
  dsl?: Record<string, unknown>
  code?: string
}): Promise<Strategy> {
  return api<Strategy>("/strategies", { method: "POST", body })
}

export function updateStrategy(
  id: string,
  body: {
    name: string
    description?: string
    tags?: string[]
    dsl?: Record<string, unknown>
    code?: string
  },
): Promise<Strategy> {
  return api<Strategy>(`/strategies/${id}`, { method: "PUT", body })
}

export function deleteStrategy(id: string): Promise<unknown> {
  return api(`/strategies/${id}`, { method: "DELETE" })
}

export function fetchStrategyRuns(id: string): Promise<StrategyRunSummary[]> {
  return api<StrategyRunSummary[]>(`/strategies/${id}/runs`)
}

export type StrategyHits = { total: number; items: Stock[] }

export function fetchStrategyHits(id: string): Promise<StrategyHits> {
  return api<StrategyHits>(`/strategies/${id}/hits`)
}

/** 落库的对话历史（挂在策略上）；proposal 的待确认状态不持久化 */
export type ChatHistoryMessage = {
  role: "user" | "assistant"
  text: string
  code?: string | null
}

export function fetchChatHistory(id: string): Promise<ChatHistoryMessage[]> {
  return api<ChatHistoryMessage[]>(`/strategies/${id}/chat`)
}

export function saveChatHistory(id: string, messages: ChatHistoryMessage[]): Promise<unknown> {
  return api(`/strategies/${id}/chat`, { method: "PUT", body: { messages } })
}

export type ChatEvent =
  | { type: "text"; delta: string }
  // 纯闲聊/追问的回合没有 dsl/code；name 是 AI 起的策略标题
  | { type: "done"; dsl?: Record<string, unknown>; code?: string; name?: string }

export type ChatTurn = { role: "user" | "assistant"; content: string }

/** Stream one turn of the AI strategy conversation over SSE. */
export async function chatStrategy(
  params: {
    text: string
    history?: ChatTurn[]
    currentDsl?: Record<string, unknown> | null
    /** 最近一次回测的 metrics（含最差调仓期/最差交易），供 AI 诊断归因 */
    lastBacktest?: Record<string, unknown> | null
  },
  onEvent: (e: ChatEvent) => void,
): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/strategies/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
    },
    body: JSON.stringify({
      text: params.text,
      history: params.history ?? [],
      currentDsl: params.currentDsl ?? null,
      lastBacktest: params.lastBacktest ?? null,
    }),
  })
  if (!res.ok) throw new Error(`对话请求失败 (${res.status})`)
  if (!res.body) throw new Error("流式响应不可用")

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ""
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const parts = buf.split("\n\n")
    buf = parts.pop() ?? ""
    for (const part of parts) {
      const line = part.trim()
      if (line.startsWith("data: ")) {
        onEvent(JSON.parse(line.slice(6)) as ChatEvent)
      }
    }
  }
}
