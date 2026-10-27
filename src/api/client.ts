const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api"

const TOKEN_KEY = "kairos_token"

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

type Opts = {
  method?: string
  body?: unknown
  auth?: boolean
}

export async function api<T = unknown>(path: string, opts: Opts = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" }
  const token = getToken()
  if (token) headers["Authorization"] = `Bearer ${token}`

  const res = await fetch(`${BASE_URL}${path}`, {
    method: opts.method ?? "GET",
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  })

  // 登录 / 注册接口的 401 是凭证错误，交给下面按 detail 报错，不当作登录过期
  const isCredentialCall = path === "/auth/login" || path === "/auth/register"
  if (res.status === 401 && !isCredentialCall) {
    setToken(null)
    // 只在应用内跳登录；首页等公开页面登录过期时只清 token，不打断浏览
    if (location.pathname.startsWith("/app")) {
      location.href = "/login"
    }
    throw new ApiError(401, "未登录或登录已过期")
  }

  if (!res.ok) {
    let detail = `请求失败 (${res.status})`
    try {
      const data = await res.json()
      detail = data.detail ?? detail
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail)
  }

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const API_BASE_URL = BASE_URL
