import { api, API_BASE_URL, getToken } from "@/api/client"
import type { Stock } from "@/lib/mock-data"

export type Strategy = {
  id: string
  name: string
  description: string
  tags: string[]
  dsl: Record<string, unknown>
  code: string
  hitCount: number
  createdAt: string
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

export function deleteStrategy(id: string): Promise<unknown> {
  return api(`/strategies/${id}`, { method: "DELETE" })
}

export type StrategyHits = { total: number; items: Stock[] }

export function fetchStrategyHits(id: string): Promise<StrategyHits> {
  return api<StrategyHits>(`/strategies/${id}/hits`)
}

export type ChatEvent =
  | { type: "text"; delta: string }
  | { type: "done"; dsl: Record<string, unknown>; code: string }

/** Stream the AI strategy generation over SSE. */
export async function chatStrategy(
  text: string,
  onEvent: (e: ChatEvent) => void,
): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/strategies/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
    },
    body: JSON.stringify({ text }),
  })
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
