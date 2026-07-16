import { Routes, Route } from "react-router-dom"
import { AppShell } from "@/components/layout/app-shell"
import { LandingPage } from "@/pages/landing-page"
import { DashboardPage } from "@/pages/dashboard-page"
import { ScreenerPage } from "@/pages/screener-page"
import { StrategyBuilderPage } from "@/pages/strategy-builder-page"
import { WatchlistPage } from "@/pages/watchlist-page"

function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route
        path="/app"
        element={
          <AppShell>
            <DashboardPage />
          </AppShell>
        }
      />
      <Route
        path="/app/screener"
        element={
          <AppShell>
            <ScreenerPage />
          </AppShell>
        }
      />
      <Route
        path="/app/strategy"
        element={
          <AppShell>
            <StrategyBuilderPage />
          </AppShell>
        }
      />
      <Route
        path="/app/watchlist"
        element={
          <AppShell>
            <WatchlistPage />
          </AppShell>
        }
      />
    </Routes>
  )
}

export default App
