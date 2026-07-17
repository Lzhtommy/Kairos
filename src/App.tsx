import { Routes, Route, Navigate } from "react-router-dom"
import type { ReactNode } from "react"
import { AppShell } from "@/components/layout/app-shell"
import { LandingPage } from "@/pages/landing-page"
import { DashboardPage } from "@/pages/dashboard-page"
import { ScreenerPage } from "@/pages/screener-page"
import { StrategyBuilderPage } from "@/pages/strategy-builder-page"
import { WatchlistPage } from "@/pages/watchlist-page"
import { LoginPage } from "@/pages/login-page"
import { useAuth } from "@/lib/auth"

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) {
    return (
      <div className="flex min-h-[100dvh] items-center justify-center text-sm text-muted-foreground">
        加载中…
      </div>
    )
  }
  if (!user) return <Navigate to="/login" replace />
  return <AppShell>{children}</AppShell>
}

function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/app" element={<RequireAuth><DashboardPage /></RequireAuth>} />
      <Route path="/app/screener" element={<RequireAuth><ScreenerPage /></RequireAuth>} />
      <Route path="/app/strategy" element={<RequireAuth><StrategyBuilderPage /></RequireAuth>} />
      <Route path="/app/watchlist" element={<RequireAuth><WatchlistPage /></RequireAuth>} />
    </Routes>
  )
}

export default App
