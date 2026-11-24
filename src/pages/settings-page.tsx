import { useEffect, useState } from "react"
import { toast } from "sonner"
import { BellRinging, Key, Newspaper, PaperPlaneTilt, SignOut, UserCircle } from "@phosphor-icons/react"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { useAuth } from "@/lib/auth"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { fetchSettings, updateSettings, testNotification, testNewsPush, type UserSettings } from "@/api/settings"
import { changePassword } from "@/api/auth"

const CHANNEL_LABELS: Record<string, string> = {
  none: "关闭",
  email: "邮件",
  webhook: "Webhook（企业微信 / 飞书）",
}

const NEWS_LEVEL_LABELS: Record<UserSettings["newsPushLevel"], string> = {
  off: "关闭",
  top: "只推重大事件（AI 评 5 分，每天几条）",
  important: "重大事件 + 要闻（另含 4 分且 4 家以上报道的，每天约二三十条）",
}

const TARGET_PLACEHOLDER: Record<string, string> = {
  email: "you@example.com",
  webhook: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…",
}

export function SettingsPage() {
  const { user, logout } = useAuth()
  const [pw, setPw] = useState({ old: "", next: "", confirm: "" })
  const [changingPw, setChangingPw] = useState(false)
  const [pwOpen, setPwOpen] = useState(false)
  const pwMismatch = pw.confirm !== "" && pw.confirm !== pw.next
  const [settings, setSettings] = useState<UserSettings | null>(null)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testingNews, setTestingNews] = useState(false)

  useEffect(() => {
    fetchSettings()
      .then(setSettings)
      .catch(() => toast.error("设置加载失败"))
  }, [])

  async function save() {
    if (!settings) return
    setSaving(true)
    try {
      setSettings(await updateSettings(settings))
      toast.success("设置已保存")
    } catch {
      toast.error("保存失败，请重试")
    } finally {
      setSaving(false)
    }
  }

  async function submitPassword(e: React.FormEvent) {
    e.preventDefault()
    if (pw.next !== pw.confirm) {
      toast.error("两次输入的新密码不一致")
      return
    }
    setChangingPw(true)
    try {
      await changePassword(pw.old, pw.next)
      setPw({ old: "", next: "", confirm: "" })
      setPwOpen(false)
      toast.success("密码已修改，其他设备上的登录已退出")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "修改失败，请重试")
    } finally {
      setChangingPw(false)
    }
  }

  async function sendTest() {
    if (!settings) return
    setTesting(true)
    try {
      await updateSettings(settings) // 先落库再测，测试的是真实生效的配置
      const res = await testNotification()
      if (res.ok) toast.success(res.detail)
      else toast.error(res.detail)
    } catch {
      toast.error("测试请求失败")
    } finally {
      setTesting(false)
    }
  }

  async function sendNewsTest() {
    if (!settings) return
    setTestingNews(true)
    try {
      setSettings(await updateSettings(settings))
      const res = await testNewsPush()
      if (res.ok) toast.success(res.detail)
      else toast.error(res.detail)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "测试请求失败")
    } finally {
      setTestingNews(false)
    }
  }

  async function saveNews() {
    if (!settings) return
    setSaving(true)
    try {
      setSettings(await updateSettings(settings))
      toast.success("设置已保存")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败，请重试")
    } finally {
      setSaving(false)
    }
  }

  if (!settings) {
    return <div className="p-6 text-sm text-muted-foreground">加载中…</div>
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6 px-4 py-6 lg:px-6">
      <div>
        <h1 className="text-lg font-semibold text-foreground">设置</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">通知与账户偏好</p>
      </div>

      <section className="space-y-4 rounded-lg border border-border p-5">
        <div className="flex items-center gap-2">
          <UserCircle className="size-4 text-primary" />
          <h2 className="text-sm font-medium text-foreground">账户</h2>
        </div>
        <div className="flex items-center gap-3">
          <Avatar className="size-10">
            <AvatarFallback className="bg-primary/15 text-primary text-sm font-medium">
              {user?.username?.[0]?.toUpperCase() ?? "K"}
            </AvatarFallback>
          </Avatar>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <p className="truncate text-sm font-medium text-foreground">{user?.username}</p>
              {user?.tier && (
                <span className="shrink-0 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                  {user.tier}
                </span>
              )}
            </div>
          </div>
          <Button variant="outline" size="sm" className="gap-1.5" onClick={() => setPwOpen(true)}>
            <Key className="size-3.5" />
            修改密码
          </Button>
          <Button variant="outline" size="sm" className="gap-1.5" onClick={logout}>
            <SignOut className="size-3.5" />
            退出登录
          </Button>
        </div>
      </section>

      <Dialog
        open={pwOpen}
        onOpenChange={(o) => {
          setPwOpen(o)
          if (!o) setPw({ old: "", next: "", confirm: "" })
        }}
      >
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>修改密码</DialogTitle>
            <DialogDescription>修改后其他设备上的登录会退出</DialogDescription>
          </DialogHeader>
          <form onSubmit={submitPassword} className="space-y-3">
            {/* 让浏览器密码管理器知道改的是哪个账号 */}
            <input type="text" autoComplete="username" value={user?.username ?? ""} readOnly hidden />
            <div className="space-y-2">
              <Label htmlFor="old-password" className="text-xs text-muted-foreground">
                当前密码
              </Label>
              <Input
                id="old-password"
                type="password"
                required
                autoComplete="current-password"
                value={pw.old}
                onChange={(e) => setPw({ ...pw, old: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="new-password" className="text-xs text-muted-foreground">
                新密码
              </Label>
              <Input
                id="new-password"
                type="password"
                required
                minLength={6}
                autoComplete="new-password"
                value={pw.next}
                onChange={(e) => setPw({ ...pw, next: e.target.value })}
                placeholder="至少 6 位"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="confirm-new-password" className="text-xs text-muted-foreground">
                确认新密码
              </Label>
              <Input
                id="confirm-new-password"
                type="password"
                required
                autoComplete="new-password"
                value={pw.confirm}
                onChange={(e) => setPw({ ...pw, confirm: e.target.value })}
                aria-invalid={pwMismatch}
              />
              {pwMismatch && <p className="text-xs text-destructive">两次输入的新密码不一致</p>}
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" size="sm" onClick={() => setPwOpen(false)}>
                取消
              </Button>
              <Button type="submit" size="sm" disabled={changingPw || pwMismatch}>
                {changingPw ? "修改中…" : "确认修改"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <section className="space-y-5 rounded-lg border border-border p-5">
        <div className="flex items-center gap-2">
          <Newspaper className="size-4 text-primary" />
          <h2 className="text-sm font-medium text-foreground">快讯推送</h2>
        </div>
        <p className="text-xs leading-relaxed text-muted-foreground">
          {"快讯里出现重要事件时，AI 打分完成后（通常在首发后 1 分钟内）推送到飞书。全天候运行，隔夜的海外大事也会推；同一件事的后续不重复推。"}
        </p>

        <div className="space-y-2">
          <Label className="text-xs text-muted-foreground">飞书机器人地址</Label>
          <Input
            value={settings.newsWebhook}
            onChange={(e) => setSettings({ ...settings, newsWebhook: e.target.value })}
            placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/…"
          />
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            在飞书里建一个群（可以只有你自己），群设置 → 群机器人 → 添加「自定义机器人」，复制 webhook 地址。
            安全设置不要开「签名校验」，可以设自定义关键词「Kairos」。
          </p>
        </div>

        <div className="space-y-2">
          <Label className="text-xs text-muted-foreground">推送范围</Label>
          <Select
            value={settings.newsPushLevel}
            onValueChange={(v) =>
              setSettings({
                ...settings,
                newsPushLevel: (v ?? "off") as UserSettings["newsPushLevel"],
              })
            }
          >
            <SelectTrigger className="w-full">
              <SelectValue>{(v: string) => NEWS_LEVEL_LABELS[v as UserSettings["newsPushLevel"]] ?? v}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {Object.entries(NEWS_LEVEL_LABELS).map(([v, label]) => (
                <SelectItem key={v} value={v}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex gap-2">
          <Button size="sm" onClick={saveNews} disabled={saving}>
            {saving ? "保存中…" : "保存"}
          </Button>
          {settings.newsWebhook.trim() && (
            <Button size="sm" variant="outline" className="gap-1.5" onClick={sendNewsTest} disabled={testingNews}>
              <PaperPlaneTilt className="size-3.5" />
              {testingNews ? "发送中…" : "发送测试推送"}
            </Button>
          )}
        </div>
      </section>

      <section className="space-y-5 rounded-lg border border-border p-5">
        <div className="flex items-center gap-2">
          <BellRinging className="size-4 text-primary" />
          <h2 className="text-sm font-medium text-foreground">策略日报通知</h2>
        </div>
        <p className="text-xs leading-relaxed text-muted-foreground">
          每个交易日收盘后（约 15:25），系统自动运行你保存的全部策略。当某个策略的命中股票
          出现新进或调出时，按下面的渠道推送给你；命中无变化的日子不打扰。
        </p>

        <div className="space-y-2">
          <Label className="text-xs text-muted-foreground">通知渠道</Label>
          <Select
            value={settings.notifyChannel}
            onValueChange={(v) =>
              setSettings({
                ...settings,
                notifyChannel: (v ?? "none") as UserSettings["notifyChannel"],
              })
            }
          >
            <SelectTrigger className="w-full">
              <SelectValue>{(v: string) => CHANNEL_LABELS[v] ?? v}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {Object.entries(CHANNEL_LABELS).map(([v, label]) => (
                <SelectItem key={v} value={v}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {settings.notifyChannel !== "none" && (
          <div className="space-y-2">
            <Label className="text-xs text-muted-foreground">
              {settings.notifyChannel === "email" ? "接收邮箱" : "Webhook 地址"}
            </Label>
            <Input
              value={settings.notifyTarget}
              onChange={(e) => setSettings({ ...settings, notifyTarget: e.target.value })}
              placeholder={TARGET_PLACEHOLDER[settings.notifyChannel]}
            />
            {settings.notifyChannel === "webhook" && (
              <p className="text-[11px] text-muted-foreground">
                支持企业微信群机器人、飞书自定义机器人，其余地址按通用 JSON POST。
              </p>
            )}
          </div>
        )}

        <div className="flex gap-2">
          <Button size="sm" onClick={save} disabled={saving}>
            {saving ? "保存中…" : "保存"}
          </Button>
          {settings.notifyChannel !== "none" && (
            <Button size="sm" variant="outline" className="gap-1.5" onClick={sendTest} disabled={testing}>
              <PaperPlaneTilt className="size-3.5" />
              {testing ? "发送中…" : "发送测试通知"}
            </Button>
          )}
        </div>
      </section>
    </div>
  )
}
