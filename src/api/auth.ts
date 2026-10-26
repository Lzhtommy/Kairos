import { api, setToken } from "@/api/client"

export type User = {
  id: number
  username: string
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

/** 改密后服务端会让旧 token 全部失效，这里换上新签发的 token 保持当前会话 */
export async function changePassword(oldPassword: string, newPassword: string): Promise<User> {
  const res = await api<TokenResp>("/auth/password", {
    method: "POST",
    body: { old_password: oldPassword, new_password: newPassword },
  })
  setToken(res.token)
  return res.user
}

export function logout() {
  setToken(null)
}
