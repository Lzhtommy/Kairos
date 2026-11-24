import { api } from "@/api/client"

export type UserSettings = {
  notifyChannel: "none" | "email" | "webhook"
  notifyTarget: string
  /** 快讯要闻推送：off / top 只推重大 / important 重大 + 要闻 */
  newsPushLevel: "off" | "top" | "important"
  /** 飞书自定义机器人地址 */
  newsWebhook: string
}

export function fetchSettings(): Promise<UserSettings> {
  return api<UserSettings>("/me/settings")
}

export function updateSettings(body: UserSettings): Promise<UserSettings> {
  return api<UserSettings>("/me/settings", { method: "PUT", body })
}

export function testNotification(): Promise<{ ok: boolean; detail: string }> {
  return api<{ ok: boolean; detail: string }>("/me/settings/test", { method: "POST" })
}

export function testNewsPush(): Promise<{ ok: boolean; detail: string }> {
  return api<{ ok: boolean; detail: string }>("/me/settings/test-news", { method: "POST" })
}
