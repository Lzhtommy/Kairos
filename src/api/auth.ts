import { api, setToken } from "@/api/client"

export type User = {
  id: number
  email: string
  nickname: string
  tier: string
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
): Promise<User> {
  const res = await api<TokenResp>("/auth/register", {
    method: "POST",
    body: { email, password, nickname },
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
