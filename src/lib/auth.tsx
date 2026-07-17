import { createContext, useContext, useEffect, useState, type ReactNode } from "react"
import { fetchMe, logout as apiLogout, type User } from "@/api/auth"
import { getToken } from "@/api/client"

type AuthState = {
  user: User | null
  loading: boolean
  setUser: (u: User | null) => void
  logout: () => void
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!getToken()) {
      setLoading(false)
      return
    }
    fetchMe()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false))
  }, [])

  function logout() {
    apiLogout()
    setUser(null)
    location.href = "/login"
  }

  return (
    <AuthContext.Provider value={{ user, loading, setUser, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error("useAuth must be used within AuthProvider")
  return ctx
}
