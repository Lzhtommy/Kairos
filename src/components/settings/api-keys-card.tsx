import { useEffect, useState } from "react"
import { toast } from "sonner"
import { Copy, Plugs, Plus, Trash } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { createApiKey, fetchApiKeys, MCP_URL, revokeApiKey, type ApiKeyList } from "@/api/api-keys"

function copy(text: string, what: string) {
  navigator.clipboard.writeText(text).then(
    () => toast.success(`${what}已复制`),
    () => toast.error("复制失败，请手动选中复制"),
  )
}

function fmt(iso: string | null) {
  return iso ? new Date(iso).toLocaleString("zh-CN", { hour12: false }).slice(0, 16) : "—"
}

/** 各客户端的接入配置；key 为空时用占位符 */
function snippets(key: string) {
  const k = key || "<你的 API Key>"
  return [
    {
      label: "Claude Code",
      text: `claude mcp add --transport http kairos ${MCP_URL} --header "Authorization: Bearer ${k}"`,
    },
    {
      label: "通用 JSON 配置（Cursor / Cline 等）",
      text: JSON.stringify(
        { mcpServers: { kairos: { type: "http", url: MCP_URL, headers: { Authorization: `Bearer ${k}` } } } },
        null,
        2,
      ),
    },
  ]
}

function Snippets({ apiKey }: { apiKey: string }) {
  return (
    <div className="space-y-3">
      {snippets(apiKey).map((s) => (
        <div key={s.label} className="space-y-1">
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-muted-foreground">{s.label}</span>
            <Button variant="ghost" size="sm" className="h-6 gap-1 px-2 text-[11px]" onClick={() => copy(s.text, "配置")}>
              <Copy className="size-3" />
              复制
            </Button>
          </div>
          <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-md bg-muted px-3 py-2 font-mono text-[11px] leading-relaxed text-foreground">
            {s.text}
          </pre>
        </div>
      ))}
    </div>
  )
}

/** 近 30 天每日调用次数：单序列柱图，悬停看当天数值，默认显示今天 */
function UsageChart({ usage }: { usage: ApiKeyList["usage"] }) {
  const [hover, setHover] = useState<number | null>(null)
  const max = Math.max(1, ...usage.map((d) => d.calls))
  const shown = usage[hover ?? usage.length - 1]
  const total = usage.reduce((s, d) => s + d.calls, 0)
  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-muted-foreground">近 30 天调用 · 合计 {total.toLocaleString()} 次</span>
        {shown && (
          <span className="tabular-nums text-foreground">
            {shown.day.slice(5)}：{shown.calls.toLocaleString()} 次
          </span>
        )}
      </div>
      <div className="flex h-16 items-end gap-[2px]" onMouseLeave={() => setHover(null)} role="img"
        aria-label={`近 30 天每日调用次数，合计 ${total} 次`}>
        {usage.map((d, i) => (
          <div key={d.day} className="flex h-full flex-1 items-end" onMouseEnter={() => setHover(i)}
            title={`${d.day}：${d.calls} 次`}>
            <div
              className={`w-full rounded-t-[4px] ${hover === i ? "bg-primary" : "bg-primary/60"}`}
              style={{ height: d.calls ? `${Math.max(4, (d.calls / max) * 100)}%` : "1px" }}
            />
          </div>
        ))}
      </div>
      <div className="flex justify-between text-[10px] text-muted-foreground tabular-nums">
        <span>{usage[0]?.day.slice(5)}</span>
        <span>今天</span>
      </div>
    </div>
  )
}

export function ApiKeysCard() {
  const [data, setData] = useState<ApiKeyList | null>(null)
  const [name, setName] = useState("")
  const [creating, setCreating] = useState(false)
  const [created, setCreated] = useState<string | null>(null)
  const [revoking, setRevoking] = useState<number | null>(null)

  const load = () =>
    fetchApiKeys()
      .then(setData)
      .catch(() => toast.error("API Key 加载失败"))

  useEffect(() => {
    load()
  }, [])

  async function create(e: React.FormEvent) {
    e.preventDefault()
    setCreating(true)
    try {
      const res = await createApiKey(name.trim() || "未命名")
      setCreated(res.key)
      setName("")
      load()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "创建失败")
    } finally {
      setCreating(false)
    }
  }

  async function revoke(id: number) {
    if (!window.confirm("吊销后使用这个 Key 的 agent 会立即无法调用，确定吗？")) return
    setRevoking(id)
    try {
      await revokeApiKey(id)
      toast.success("已吊销")
      load()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "吊销失败")
    } finally {
      setRevoking(null)
    }
  }

  const active = data?.keys.filter((k) => !k.revokedAt) ?? []
  const revoked = data?.keys.filter((k) => k.revokedAt) ?? []

  return (
    <section className="space-y-5 rounded-lg border border-border p-5">
      <div className="flex items-center gap-2">
        <Plugs className="size-4 text-primary" />
        <h2 className="text-sm font-medium text-foreground">数据 API（MCP）</h2>
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">
        把 Kairos 的 A 股数据接入你自己的 agent（Claude Code、Cursor 等支持 MCP 的客户端）：行情与日 K、每日估值指标、
        财报与三大报表、股东股本、两融龙虎榜大宗交易、公告、快讯、宏观利率。每个 Key 默认每天{" "}
        {data?.defaultDailyLimit ?? 1000} 次调用、每分钟 {data?.perMinute ?? 60} 次，北京时间零点重置。
      </p>

      <div className="space-y-1">
        <span className="text-[11px] text-muted-foreground">服务地址</span>
        <div className="flex items-center gap-2">
          <code className="flex-1 truncate rounded-md bg-muted px-3 py-1.5 font-mono text-xs">{MCP_URL}</code>
          <Button variant="outline" size="sm" className="gap-1.5" onClick={() => copy(MCP_URL, "地址")}>
            <Copy className="size-3.5" />
            复制
          </Button>
        </div>
      </div>

      {active.length > 0 && (
        <div className="divide-y divide-border rounded-md border border-border">
          {active.map((k) => (
            <div key={k.id} className="flex items-center gap-3 px-3 py-2.5">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-medium text-foreground">{k.name}</span>
                  <code className="shrink-0 font-mono text-[11px] text-muted-foreground">{k.prefix}…</code>
                </div>
                <div className="mt-1.5 flex items-center gap-2">
                  <div className="h-1.5 w-28 shrink-0 overflow-hidden rounded-full bg-muted">
                    <div
                      className={`h-full rounded-full ${k.usedToday >= k.dailyLimit ? "bg-destructive" : "bg-primary"}`}
                      style={{ width: `${Math.min(100, (k.usedToday / Math.max(1, k.dailyLimit)) * 100)}%` }}
                    />
                  </div>
                  <span className="text-[11px] tabular-nums text-foreground">
                    今日 {k.usedToday.toLocaleString()} / {k.dailyLimit.toLocaleString()}
                  </span>
                </div>
                <p className="mt-1 text-[11px] text-muted-foreground">
                  近 30 天 {k.used30d.toLocaleString()} 次 · 最近使用 {fmt(k.lastUsedAt)} · 创建于 {fmt(k.createdAt)}
                </p>
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="gap-1 text-destructive hover:text-destructive"
                disabled={revoking === k.id}
                onClick={() => revoke(k.id)}
              >
                <Trash className="size-3.5" />
                吊销
              </Button>
            </div>
          ))}
        </div>
      )}

      {data && data.keys.length > 0 && <UsageChart usage={data.usage} />}

      {data && active.length < data.maxKeys ? (
        <form onSubmit={create} className="flex gap-2">
          <Input
            value={name}
            maxLength={64}
            onChange={(e) => setName(e.target.value)}
            placeholder="给 Key 起个名字，如「Claude Code - 公司电脑」"
          />
          <Button type="submit" size="sm" className="shrink-0 gap-1.5" disabled={creating}>
            <Plus className="size-3.5" />
            {creating ? "创建中…" : "创建 API Key"}
          </Button>
        </form>
      ) : (
        data && <p className="text-xs text-muted-foreground">最多 {data.maxKeys} 个有效 Key，请先吊销不用的。</p>
      )}

      {active.length > 0 && (
        <details className="group">
          <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">接入配置示例</summary>
          <div className="mt-3">
            <Snippets apiKey="" />
          </div>
        </details>
      )}

      {revoked.length > 0 && (
        <p className="text-[11px] text-muted-foreground">
          已吊销：{revoked.map((k) => `${k.name}（${k.prefix}…）`).join("、")}
        </p>
      )}

      <Dialog open={created !== null} onOpenChange={(o) => !o && setCreated(null)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>API Key 已创建</DialogTitle>
            <DialogDescription>只显示这一次，关掉后无法再查看，请现在复制保存。泄露了就吊销重建。</DialogDescription>
          </DialogHeader>
          {created && (
            <div className="space-y-4">
              <div className="flex items-center gap-2">
                <code className="flex-1 break-all rounded-md bg-muted px-3 py-2 font-mono text-xs">{created}</code>
                <Button size="sm" className="shrink-0 gap-1.5" onClick={() => copy(created, "API Key ")}>
                  <Copy className="size-3.5" />
                  复制
                </Button>
              </div>
              <Snippets apiKey={created} />
            </div>
          )}
          <DialogFooter>
            <Button size="sm" onClick={() => setCreated(null)}>
              我已保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  )
}
