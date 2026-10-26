import { api } from "@/api/client"

export type Invite = {
  id: number
  code: string
  usedBy: string | null
  usedAt: string | null
  createdAt: string | null
}

export async function fetchInvites(): Promise<Invite[]> {
  return api<Invite[]>("/admin/invites")
}

export async function createInvites(count: number): Promise<Invite[]> {
  return api<Invite[]>("/admin/invites", { method: "POST", body: { count } })
}

export async function deleteInvite(id: number): Promise<void> {
  await api(`/admin/invites/${id}`, { method: "DELETE" })
}

export type AdminUser = {
  id: number
  username: string
  nickname: string
  tier: string
  isAdmin: boolean
  strategyCount: number
  createdAt: string | null
}

export async function fetchUsers(): Promise<AdminUser[]> {
  return api<AdminUser[]>("/admin/users")
}

export async function setUserAdmin(id: number, isAdmin: boolean): Promise<AdminUser> {
  return api<AdminUser>(`/admin/users/${id}`, { method: "PATCH", body: { isAdmin } })
}

export async function resetUserPassword(id: number): Promise<string> {
  const { password } = await api<{ password: string }>(`/admin/users/${id}/reset-password`, {
    method: "POST",
  })
  return password
}

export async function deleteUser(id: number): Promise<void> {
  await api(`/admin/users/${id}`, { method: "DELETE" })
}
