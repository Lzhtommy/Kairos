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
