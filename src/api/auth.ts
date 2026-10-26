import { api, setToken } from "@/api/client"

export type User = {
  id: number
  username: string
  nickname: string
  tier: string
  is_admin: boolean
}

type TokenResp = { token: string; user: User }

export async function login(username: string, password: string): Promise<User> {
  const res = await api<TokenResp>("/auth/login", {
    method: "POST",
    body: { username, password },
  })
  setToken(res.token)
  return res.user
}

export async function register(
  username: string,
  password: string,
  inviteCode: string,
): Promise<User> {
  const res = await api<TokenResp>("/auth/register", {
    method: "POST",
    body: { username, password, invite_code: inviteCode },
  })
  setToken(res.token)
  return res.user
}

export async function fetchMe(): Promise<User> {
  return api<User>("/auth/me")
}

export async function updateProfile(nickname: string): Promise<User> {
  return api<User>("/auth/me", { method: "PATCH", body: { nickname } })
}

export function logout() {
  setToken(null)
}
