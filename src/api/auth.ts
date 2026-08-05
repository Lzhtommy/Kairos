import { api, setToken } from "@/api/client"

export type User = {
  id: number
  email: string
  nickname: string
  tier: string
  is_admin: boolean
}

type TokenResp = { token: string; user: User }

export async function login(email: string, password: string): Promise<User> {
  const res = await api<TokenResp>("/auth/login", {
    method: "POST",
    body: { email, password },
  })
  setToken(res.token)
  return res.user
}

export async function register(
  email: string,
  password: string,
  nickname: string,
  inviteCode: string,
): Promise<User> {
  const res = await api<TokenResp>("/auth/register", {
    method: "POST",
    body: { email, password, nickname, invite_code: inviteCode },
  })
  setToken(res.token)
  return res.user
}

export async function fetchMe(): Promise<User> {
  return api<User>("/auth/me")
}

export function logout() {
  setToken(null)
}
