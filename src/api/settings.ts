import { api } from "@/api/client"

export type UserSettings = {
  notifyChannel: "none" | "email" | "webhook"
  notifyTarget: string
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
