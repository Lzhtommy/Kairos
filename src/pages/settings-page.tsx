import { useEffect, useState } from "react"
import { toast } from "sonner"
import { BellRinging, PaperPlaneTilt, SignOut, UserCircle } from "@phosphor-icons/react"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { useAuth } from "@/lib/auth"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  fetchSettings,
  updateSettings,
  testNotification,
  type UserSettings,
} from "@/api/settings"

const CHANNEL_LABELS: Record<string, string> = {
  none: "关闭",
  email: "邮件",
  webhook: "Webhook（企业微信 / 飞书 / Server酱）",
}

const TARGET_PLACEHOLDER: Record<string, string> = {
  email: "you@example.com",
  webhook: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…",
}

export function SettingsPage() {
  const { user, logout } = useAuth()
  const [settings, setSettings] = useState<UserSettings | null>(null)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)

  useEffect(() => {
    fetchSettings().then(setSettings).catch(() => toast.error("设置加载失败"))
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
              {user?.nickname?.[0] ?? "K"}
            </AvatarFallback>
          </Avatar>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <p className="truncate text-sm font-medium text-foreground">{user?.nickname}</p>
              {user?.tier && (
                <span className="shrink-0 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                  {user.tier}
                </span>
              )}
            </div>
            <p className="truncate text-xs text-muted-foreground">{user?.email}</p>
          </div>
          <Button variant="outline" size="sm" className="gap-1.5" onClick={logout}>
            <SignOut className="size-3.5" />
            退出登录
          </Button>
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
              setSettings({ ...settings, notifyChannel: (v ?? "none") as UserSettings["notifyChannel"] })
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
                支持企业微信群机器人、飞书自定义机器人、Server酱，其余地址按通用 JSON POST。
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
