import { useState } from "react"
import { Link } from "react-router-dom"
import { List, X } from "@phosphor-icons/react"
import { Logo } from "@/components/layout/logo"
import { buttonVariants } from "@/components/ui/button"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { useAuth } from "@/lib/auth"
import { cn } from "@/lib/utils"

const LINKS = [
  { href: "#features", label: "功能" },
  { href: "#strategy", label: "策略工坊" },
  { href: "#coverage", label: "数据覆盖" },
]

export function MarketingNav() {
  const [open, setOpen] = useState(false)
  const { user } = useAuth()

  return (
    <header className="sticky top-0 z-40 border-b border-border/60 bg-background/80 backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-7xl items-center px-4 lg:px-6">
        <Link to="/">
          <Logo />
        </Link>
        <nav className="ml-10 hidden items-center gap-7 md:flex">
          {LINKS.map((l) => (
            <a key={l.href} href={l.href} className="text-sm text-muted-foreground transition-colors hover:text-foreground">
              {l.label}
            </a>
          ))}
        </nav>
        <div className="ml-auto hidden items-center gap-2 md:flex">
          {user ? (
            <Link
              to="/app"
              className="flex items-center gap-2 rounded-md py-1 pl-1.5 pr-2.5 transition-colors hover:bg-muted"
            >
              <Avatar className="size-7">
                <AvatarFallback className="bg-primary/15 text-primary text-xs font-medium">
                  {user.nickname?.[0] ?? "K"}
                </AvatarFallback>
              </Avatar>
              <span className="text-sm font-medium text-foreground">{user.nickname}</span>
            </Link>
          ) : (
            <Link to="/login" className={cn(buttonVariants({ variant: "ghost", size: "sm" }))}>
              登录
            </Link>
          )}
          <Link to="/app" className={cn(buttonVariants({ size: "sm" }))}>
            {user ? "进入应用" : "开始使用"}
          </Link>
        </div>
        <button
          className="ml-auto md:hidden"
          onClick={() => setOpen((v) => !v)}
          aria-label="打开菜单"
        >
          {open ? <X className="size-5" /> : <List className="size-5" />}
        </button>
      </div>
      {open && (
        <div className="border-t border-border/60 px-4 py-3 md:hidden">
          <nav className="flex flex-col gap-3">
            {LINKS.map((l) => (
              <a key={l.href} href={l.href} className="text-sm text-muted-foreground" onClick={() => setOpen(false)}>
                {l.label}
              </a>
            ))}
            <Link to="/app" className={cn(buttonVariants({ size: "sm" }), "mt-1 w-full")}>
              {user ? `进入应用（${user.nickname}）` : "开始使用"}
            </Link>
          </nav>
        </div>
      )}
    </header>
  )
}
