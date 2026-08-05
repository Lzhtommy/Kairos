import { type ReactNode, useState } from "react"
import { Link, useLocation } from "react-router-dom"
import {
  ChartLineUp,
  ChartBar,
  FunnelSimple,
  Robot,
  Sparkle,
  Star,
  Sun,
  Moon,
  Bell,
  List,
  Ticket,
  X,
} from "@phosphor-icons/react"
import { GearSix, SignOut } from "@phosphor-icons/react"
import { Logo } from "@/components/layout/logo"
import { useTheme } from "@/lib/theme"
import { useAuth } from "@/lib/auth"
import { cn } from "@/lib/utils"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"

const NAV_ITEMS = [
  { to: "/app", label: "大盘", icon: ChartLineUp, end: true },
  { to: "/app/screener", label: "选股", icon: FunnelSimple },
  { to: "/app/strategy", label: "策略工坊", icon: Sparkle },
  { to: "/app/backtest", label: "回测", icon: ChartBar },
  { to: "/app/paper", label: "模拟盘", icon: Robot },
  { to: "/app/watchlist", label: "自选", icon: Star },
  { to: "/app/settings", label: "设置", icon: GearSix },
  { to: "/app/invites", label: "邀请码", icon: Ticket, adminOnly: true },
]

function QuickActions() {
  const { theme, toggle } = useTheme()
  return (
    <>
      <Tooltip>
        <TooltipTrigger
          render={
            <button
              className="flex size-9 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-label="通知"
            >
              <Bell className="size-4.5" />
            </button>
          }
        />
        <TooltipContent>通知</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger
          render={
            <button
              onClick={toggle}
              className="flex size-9 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-label="切换主题"
            >
              {theme === "dark" ? (
                <Sun className="size-4.5" />
              ) : (
                <Moon className="size-4.5" />
              )}
            </button>
          }
        />
        <TooltipContent>切换{theme === "dark" ? "浅色" : "深色"}模式</TooltipContent>
      </Tooltip>
    </>
  )
}

/** 登录信息区：头像 + 昵称/套餐 + 退出，桌面侧栏与移动端抽屉共用 */
function UserFooter() {
  const { user, logout } = useAuth()
  const initial = user?.nickname?.[0] ?? "K"
  return (
    <div className="flex items-center gap-2.5 rounded-md px-2 py-2">
      <Avatar className="size-7">
        <AvatarFallback className="bg-primary/15 text-primary text-xs font-medium">
          {initial}
        </AvatarFallback>
      </Avatar>
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs font-medium text-foreground">
          {user?.nickname ?? "未登录"}
        </p>
        <p className="truncate text-[11px] text-muted-foreground">
          {user?.email ?? ""}
        </p>
      </div>
      {user?.tier && (
        <span className="shrink-0 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
          {user.tier}
        </span>
      )}
      <Tooltip>
        <TooltipTrigger
          render={
            <button
              onClick={logout}
              className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-label="退出登录"
            >
              <SignOut className="size-4" />
            </button>
          }
        />
        <TooltipContent>退出登录</TooltipContent>
      </Tooltip>
    </div>
  )
}

export function AppShell({ children }: { children: ReactNode }) {
  const location = useLocation()
  const { user } = useAuth()
  const [mobileOpen, setMobileOpen] = useState(false)
  const navItems = NAV_ITEMS.filter((item) => !item.adminOnly || user?.is_admin)

  const isActive = (to: string, end?: boolean) =>
    end ? location.pathname === to : location.pathname.startsWith(to)

  return (
    // 固定视口高度：滚动发生在 main 内部，侧栏（含底部登录信息）常驻不随内容卷走
    <div className="flex h-[100dvh] overflow-hidden bg-background">
      {/* Sidebar - desktop */}
      <aside className="hidden lg:flex w-56 shrink-0 flex-col border-r border-border bg-sidebar">
        <div className="flex h-16 items-center pl-5 pr-3">
          <Link to="/">
            <Logo />
          </Link>
          <div className="ml-auto flex items-center gap-0.5">
            <QuickActions />
          </div>
        </div>
        <nav className="min-h-0 flex-1 space-y-1 overflow-y-auto px-3 py-2">
          {navItems.map((item) => {
            const active = isActive(item.to, item.end)
            return (
              <Link
                key={item.to}
                to={item.to}
                className={cn(
                  "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  active
                    ? "bg-sidebar-accent text-sidebar-accent-foreground"
                    : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
                )}
              >
                <item.icon weight={active ? "fill" : "regular"} className="size-4.5 shrink-0" />
                {item.label}
              </Link>
            )
          })}
        </nav>
        <div className="border-t border-border p-3">
          <UserFooter />
        </div>
      </aside>

      {/* Mobile sidebar */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div
            className="absolute inset-0 bg-background/80 backdrop-blur-sm"
            onClick={() => setMobileOpen(false)}
          />
          <aside className="absolute inset-y-0 left-0 flex w-64 flex-col border-r border-border bg-sidebar p-3">
            <div className="mb-4 flex h-10 items-center justify-between px-2">
              <Logo />
              <button onClick={() => setMobileOpen(false)} aria-label="关闭菜单">
                <X className="size-5 text-muted-foreground" />
              </button>
            </div>
            <nav className="flex-1 space-y-1">
              {navItems.map((item) => {
                const active = isActive(item.to, item.end)
                return (
                  <Link
                    key={item.to}
                    to={item.to}
                    onClick={() => setMobileOpen(false)}
                    className={cn(
                      "flex items-center gap-2.5 rounded-md px-3 py-2.5 text-sm font-medium",
                      active
                        ? "bg-sidebar-accent text-sidebar-accent-foreground"
                        : "text-muted-foreground",
                    )}
                  >
                    <item.icon weight={active ? "fill" : "regular"} className="size-4.5" />
                    {item.label}
                  </Link>
                )
              })}
            </nav>
            <div className="border-t border-border pt-2">
              <UserFooter />
            </div>
          </aside>
        </div>
      )}

      {/* Main column */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* 桌面端图标已并入侧栏 logo 行，此顶栏只服务移动端（汉堡菜单 + 快捷图标） */}
        <header className="flex h-16 shrink-0 items-center gap-3 border-b border-border px-4 lg:hidden">
          <button
            onClick={() => setMobileOpen(true)}
            aria-label="打开菜单"
          >
            <List className="size-5.5 text-foreground" />
          </button>
          <div className="ml-auto flex items-center gap-1.5">
            <QuickActions />
          </div>
        </header>
        <main className="min-w-0 flex-1 overflow-y-auto">{children}</main>
      </div>
    </div>
  )
}
