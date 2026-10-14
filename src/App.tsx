import { Routes, Route, Navigate } from "react-router-dom"
import type { ReactNode } from "react"
import { AppShell } from "@/components/layout/app-shell"
import { LandingPage } from "@/pages/landing-page"
import { ScreenerPage } from "@/pages/screener-page"
import { SectorPage } from "@/pages/sector-page"
import { ResearchPage } from "@/pages/research-page"
import { ResearchDetailPage } from "@/pages/research-detail-page"
import { StrategyBuilderPage } from "@/pages/strategy-builder-page"
import { BacktestPage } from "@/pages/backtest-page"
import { PaperPage } from "@/pages/paper-page"
import { SettingsPage } from "@/pages/settings-page"
import { AdminInvitesPage } from "@/pages/admin-invites-page"
import { AdminUsersPage } from "@/pages/admin-users-page"
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

function RequireAdmin({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  if (user && !user.is_admin) return <Navigate to="/app" replace />
  return <>{children}</>
}

function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/app" element={<Navigate to="/app/screener" replace />} />
      <Route path="/app/screener" element={<RequireAuth><ScreenerPage /></RequireAuth>} />
      <Route path="/app/sector" element={<RequireAuth><SectorPage /></RequireAuth>} />
      <Route path="/app/research" element={<RequireAuth><ResearchPage /></RequireAuth>} />
      <Route path="/app/research/:id" element={<RequireAuth><ResearchDetailPage /></RequireAuth>} />
      <Route path="/app/strategy" element={<RequireAuth><StrategyBuilderPage /></RequireAuth>} />
      <Route path="/app/backtest" element={<RequireAuth><BacktestPage /></RequireAuth>} />
      <Route path="/app/paper" element={<RequireAuth><PaperPage /></RequireAuth>} />
      <Route path="/app/settings" element={<RequireAuth><SettingsPage /></RequireAuth>} />
      <Route
        path="/app/invites"
        element={<RequireAuth><RequireAdmin><AdminInvitesPage /></RequireAdmin></RequireAuth>}
      />
      <Route
        path="/app/users"
        element={<RequireAuth><RequireAdmin><AdminUsersPage /></RequireAdmin></RequireAuth>}
      />
    </Routes>
  )
}

export default App
