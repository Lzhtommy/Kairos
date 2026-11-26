import { api } from "@/api/client"

export type ApiKeyItem = {
  id: number
  name: string
  /** 明文前 10 位，列表里辨认用 */
  prefix: string
  dailyLimit: number
  usedToday: number
  /** 近 30 天合计 */
  used30d: number
  createdAt: string | null
  lastUsedAt: string | null
  revokedAt: string | null
}

export type ApiKeyList = {
  keys: ApiKeyItem[]
  /** 近 30 天每天的调用次数合计（含已吊销的 Key），按日期正序 */
  usage: { day: string; calls: number }[]
  maxKeys: number
  perMinute: number
  defaultDailyLimit: number
}

export const fetchApiKeys = () => api<ApiKeyList>("/me/api-keys")

/** 返回值里的 key 是明文，只此一次 */
export const createApiKey = (name: string) =>
  api<ApiKeyItem & { key: string }>("/me/api-keys", { method: "POST", body: { name } })

export const revokeApiKey = (id: number) => api<{ ok: boolean }>(`/me/api-keys/${id}`, { method: "DELETE" })

export const MCP_URL = `${window.location.origin}/api/mcp`

export type AdminApiKeyItem = ApiKeyItem & { username: string }

export const fetchAllApiKeys = () => api<AdminApiKeyItem[]>("/admin/api-keys")

export const setApiKeyLimit = (id: number, dailyLimit: number) =>
  api<ApiKeyItem>(`/admin/api-keys/${id}`, { method: "PATCH", body: { dailyLimit } })

export const adminRevokeApiKey = (id: number) =>
  api<{ ok: boolean }>(`/admin/api-keys/${id}`, { method: "DELETE" })
