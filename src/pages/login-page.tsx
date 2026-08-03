import { useState } from "react"
import { useNavigate, Link, Navigate } from "react-router-dom"
import { toast } from "sonner"
import { Logo } from "@/components/layout/logo"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { login, register } from "@/api/auth"
import { useAuth } from "@/lib/auth"

export function LoginPage() {
  const navigate = useNavigate()
  const { user, setUser } = useAuth()
  const [mode, setMode] = useState<"login" | "register">("login")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [nickname, setNickname] = useState("")
  const [busy, setBusy] = useState(false)

  // 已登录还访问 /login → 直接回应用
  if (user) return <Navigate to="/app" replace />

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    try {
      const user =
        mode === "login"
          ? await login(email, password)
          : await register(email, password, nickname || email.split("@")[0])
      setUser(user)
      toast.success(mode === "login" ? "登录成功" : "注册成功")
      navigate("/app")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "操作失败")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-[100dvh] items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="flex justify-center">
          <Link to="/">
            <Logo />
          </Link>
        </div>
        <Tabs value={mode} onValueChange={(v) => setMode(v as typeof mode)}>
          <TabsList className="w-full">
            <TabsTrigger value="login" className="flex-1">登录</TabsTrigger>
            <TabsTrigger value="register" className="flex-1">注册</TabsTrigger>
          </TabsList>
          <TabsContent value={mode}>
            <form onSubmit={submit} className="space-y-4 rounded-lg border border-border p-5">
              {mode === "register" && (
                <div className="space-y-1.5">
                  <Label htmlFor="nickname">昵称</Label>
                  <Input
                    id="nickname"
                    value={nickname}
                    onChange={(e) => setNickname(e.target.value)}
                    placeholder="你的名字"
                  />
                </div>
              )}
              <div className="space-y-1.5">
                <Label htmlFor="email">邮箱</Label>
                <Input
                  id="email"
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="password">密码</Label>
                <Input
                  id="password"
                  type="password"
                  required
                  minLength={6}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="至少 6 位"
                />
              </div>
              <Button type="submit" className="w-full" disabled={busy}>
                {busy ? "处理中…" : mode === "login" ? "登录" : "注册并登录"}
              </Button>
            </form>
          </TabsContent>
        </Tabs>
        <p className="text-center text-xs text-muted-foreground">
          演示环境，数据仅供参考，不构成投资建议
        </p>
      </div>
    </div>
  )
}
