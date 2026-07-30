import { useMemo, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  Sparkle,
  PaperPlaneRight,
  Play,
  FloppyDisk,
  Plus,
  Robot,
  Trash,
} from "@phosphor-icons/react"
import { CodeBlock } from "@/components/strategy/code-block"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  fetchStrategies,
  createStrategy,
  deleteStrategy,
  chatStrategy,
  type Strategy,
} from "@/api/strategies"
import { submitBacktest, fetchBacktest, type BacktestResult } from "@/api/backtests"
import { cn } from "@/lib/utils"

type Message = { role: "user" | "assistant"; text: string; code?: string }

const PLACEHOLDER_CODE = `# 在左侧用自然语言描述你的选股逻辑，\n# AI 会在这里生成可回测的策略代码。`

function EquityCurve({ curve }: { curve: { t: string; v: number }[] }) {
  if (curve.length < 2) return null
  const values = curve.map((p) => p.v)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  const w = 100
  const h = 40
  const pts = curve.map((p, i) => {
    const x = (i / (curve.length - 1)) * w
    const y = h - ((p.v - min) / range) * h
    return `${x.toFixed(2)},${y.toFixed(2)}`
  })
  const up = values[values.length - 1] >= values[0]
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="h-24 w-full">
      <polyline
        points={pts.join(" ")}
        fill="none"
        stroke={up ? "var(--color-up, #16a34a)" : "var(--color-down, #dc2626)"}
        strokeWidth="1"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  )
}

export function StrategyBuilderPage() {
  const qc = useQueryClient()
  const strategiesQ = useQuery({ queryKey: ["strategies"], queryFn: fetchStrategies })
  const strategies = useMemo(() => strategiesQ.data ?? [], [strategiesQ.data])

  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState("")
  const [thinking, setThinking] = useState(false)
  const [draftDsl, setDraftDsl] = useState<Record<string, unknown> | null>(null)
  const [draftCode, setDraftCode] = useState<string>("")
  const [lastPrompt, setLastPrompt] = useState("")
  const [savedId, setSavedId] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [backtest, setBacktest] = useState<BacktestResult | null>(null)
  const [btRunning, setBtRunning] = useState(false)
  const [tab, setTab] = useState("code")
  const scrollRef = useRef<HTMLDivElement>(null)

  async function send() {
    const text = input.trim()
    if (!text || thinking) return
    setInput("")
    setLastPrompt(text)
    setSavedId(null)
    setBacktest(null)
    setMessages((m) => [...m, { role: "user", text }])
    setThinking(true)

    const assistantIndex = messages.length + 1
    setMessages((m) => [...m, { role: "assistant", text: "" }])

    try {
      await chatStrategy(text, (ev) => {
        if (ev.type === "text") {
          setMessages((m) => {
            const next = [...m]
            const cur = next[assistantIndex]
            if (cur) next[assistantIndex] = { ...cur, text: cur.text + ev.delta }
            return next
          })
        } else if (ev.type === "done") {
          setDraftDsl(ev.dsl)
          setDraftCode(ev.code)
          setMessages((m) => {
            const next = [...m]
            const cur = next[assistantIndex]
            if (cur) next[assistantIndex] = { ...cur, code: ev.code }
            return next
          })
        }
      })
    } catch {
      toast.error("生成失败，请重试")
    } finally {
      setThinking(false)
      requestAnimationFrame(() => {
        scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
      })
    }
  }

  async function save(): Promise<string | null> {
    if (!draftDsl) {
      toast.error("请先用 AI 生成一个策略")
      return null
    }
    setSaving(true)
    try {
      const name = lastPrompt.slice(0, 16) || "未命名策略"
      const created = await createStrategy({
        name,
        description: lastPrompt,
        tags: ["AI"],
        dsl: draftDsl,
        code: draftCode,
      })
      setSavedId(created.id)
      qc.invalidateQueries({ queryKey: ["strategies"] })
      toast.success(`已保存策略「${created.name}」，命中 ${created.hitCount} 只`)
      return created.id
    } catch {
      toast.error("保存失败")
      return null
    } finally {
      setSaving(false)
    }
  }

  async function runBacktest() {
    let id = savedId
    if (!id) id = await save()
    if (!id) return
    setBtRunning(true)
    setBacktest(null)
    setTab("backtest")
    try {
      const { id: bid } = await submitBacktest(id)
      for (let i = 0; i < 30; i++) {
        const res = await fetchBacktest(bid)
        if (res.status === "done" || res.status === "failed") {
          setBacktest(res)
          if (res.status === "failed") toast.error("回测失败：" + (res.error ?? ""))
          break
        }
        await new Promise((r) => setTimeout(r, 500))
      }
    } catch {
      toast.error("回测请求失败")
    } finally {
      setBtRunning(false)
    }
  }

  function confirmDelete(s: Strategy) {
    toast(`确定删除策略「${s.name}」？`, {
      action: { label: "删除", onClick: () => void doDelete(s) },
    })
  }

  async function doDelete(s: Strategy) {
    try {
      await deleteStrategy(s.id)
      qc.invalidateQueries({ queryKey: ["strategies"] })
      qc.removeQueries({ queryKey: ["strategyHits", s.id] })
      if (savedId === s.id) setSavedId(null) // 当前载入的被删了，退回未保存草稿
      toast.success(`已删除「${s.name}」`)
    } catch {
      toast.error("删除失败，请重试")
    }
  }

  function loadStrategy(s: Strategy) {
    setDraftDsl(s.dsl)
    setDraftCode(s.code)
    setSavedId(s.id)
    setLastPrompt(s.description)
    setBacktest(null)
    setMessages([
      { role: "user", text: s.description || s.name },
      { role: "assistant", text: `已载入策略「${s.name}」，可继续对话调整，或直接运行回测。`, code: s.code },
    ])
  }

  const activeCode = draftCode || PLACEHOLDER_CODE
  const metrics = backtest?.status === "done" ? backtest.metrics : null

  return (
    <div className="grid h-[calc(100dvh-4rem)] grid-cols-1 lg:grid-cols-[240px_1fr_440px]">
      {/* Strategy list */}
      <aside className="hidden border-r border-border lg:flex lg:flex-col">
        <div className="flex items-center justify-between p-3">
          <span className="text-sm font-medium text-foreground">我的策略</span>
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label="新建策略"
            onClick={() => {
              setMessages([])
              setDraftDsl(null)
              setDraftCode("")
              setSavedId(null)
              setBacktest(null)
            }}
          >
            <Plus className="size-4" />
          </Button>
        </div>
        <div className="flex-1 space-y-1 overflow-y-auto px-2 pb-3 scrollbar-thin">
          {strategies.length === 0 && (
            <p className="px-2.5 py-2 text-xs text-muted-foreground">还没有策略</p>
          )}
          {strategies.map((s) => (
            <div
              key={s.id}
              className={cn(
                "group relative rounded-md transition-colors",
                savedId === s.id
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
              )}
            >
              <button onClick={() => loadStrategy(s)} className="w-full px-2.5 py-2 text-left">
                <p className="truncate pr-5 text-sm font-medium">{s.name}</p>
                <p className="mt-0.5 truncate text-xs opacity-70">
                  {s.createdAt} · 命中 {s.hitCount}
                </p>
              </button>
              <button
                onClick={() => confirmDelete(s)}
                aria-label={`删除策略 ${s.name}`}
                className="absolute right-1.5 top-2 hidden size-6 items-center justify-center rounded text-muted-foreground hover:text-destructive group-hover:flex"
              >
                <Trash className="size-3.5" />
              </button>
            </div>
          ))}
        </div>
      </aside>

      {/* Chat */}
      <div className="flex min-w-0 flex-col border-r border-border">
        <div className="flex h-12 shrink-0 items-center px-4">
          <p className="text-sm font-medium text-foreground">策略工坊</p>
        </div>
        <div ref={scrollRef} className="flex-1 space-y-5 overflow-y-auto px-4 py-4 scrollbar-thin">
          {messages.length === 0 && (
            <div className="mx-auto mt-10 max-w-sm text-center">
              <Sparkle weight="fill" className="mx-auto size-8 text-primary/70" />
              <p className="mt-3 text-sm text-muted-foreground">
                用自然语言描述你的选股逻辑，例如：
              </p>
              <p className="mt-2 text-sm text-foreground">
                「市盈率低于行业中位数，ROE 不低于 12%，股息率不低于 3%」
              </p>
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={cn("flex gap-3", m.role === "user" && "flex-row-reverse")}>
              <Avatar className="size-7 shrink-0">
                {m.role === "assistant" ? (
                  <AvatarFallback className="bg-primary/15 text-primary">
                    <Sparkle weight="fill" className="size-3.5" />
                  </AvatarFallback>
                ) : (
                  <AvatarFallback className="bg-muted text-muted-foreground text-xs">我</AvatarFallback>
                )}
              </Avatar>
              <div
                className={cn(
                  "max-w-[85%] space-y-3 rounded-xl px-3.5 py-2.5 text-sm leading-relaxed",
                  m.role === "user"
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-foreground",
                )}
              >
                <p className="whitespace-pre-wrap">{m.text || "…"}</p>
                {m.code && (
                  <div className="-mx-1 rounded-lg border border-border/60 bg-background/60 p-3">
                    <CodeBlock code={m.code} className="overflow-x-auto" />
                  </div>
                )}
              </div>
            </div>
          ))}
          {thinking && messages[messages.length - 1]?.text === "" && (
            <div className="flex gap-3">
              <Avatar className="size-7 shrink-0">
                <AvatarFallback className="bg-primary/15 text-primary">
                  <Sparkle weight="fill" className="size-3.5" />
                </AvatarFallback>
              </Avatar>
              <div className="flex items-center gap-1 rounded-xl bg-muted px-3.5 py-2.5">
                {[0, 1, 2].map((d) => (
                  <span
                    key={d}
                    className="size-1.5 animate-bounce rounded-full bg-muted-foreground/60"
                    style={{ animationDelay: `${d * 0.12}s` }}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
        <div className="shrink-0 border-t border-border p-3">
          <div className="flex items-end gap-2 rounded-xl border border-border bg-muted/40 p-2 focus-within:border-ring">
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault()
                  send()
                }
              }}
              placeholder="描述你的选股逻辑，例如：连续三日放量上涨且换手率高于5%"
              className="min-h-9 resize-none border-none bg-transparent px-2 py-1.5 shadow-none focus-visible:ring-0"
              rows={1}
            />
            <Button size="icon" onClick={send} aria-label="发送" disabled={!input.trim() || thinking}>
              <PaperPlaneRight weight="fill" className="size-4" />
            </Button>
          </div>
          <p className="mt-1.5 px-1 text-[11px] text-muted-foreground">
            Enter 发送，Shift + Enter 换行
          </p>
        </div>
      </div>

      {/* Code / backtest preview */}
      <div className="flex min-w-0 flex-col">
        <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col">
          <div className="flex h-12 shrink-0 items-center justify-between px-4">
            <TabsList>
              <TabsTrigger value="code">策略代码</TabsTrigger>
              <TabsTrigger value="backtest">回测预览</TabsTrigger>
            </TabsList>
            <div className="flex gap-1.5">
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label="保存策略"
                onClick={save}
                disabled={saving || !draftDsl}
              >
                <FloppyDisk className="size-4" />
              </Button>
              <Button size="sm" className="gap-1.5" onClick={runBacktest} disabled={btRunning || !draftDsl}>
                <Play weight="fill" className="size-3.5" />
                {btRunning ? "回测中…" : "运行回测"}
              </Button>
            </div>
          </div>
          <TabsContent value="code" className="min-h-0 flex-1 overflow-y-auto px-4 pb-4 scrollbar-thin">
            <div className="rounded-lg border border-border bg-muted/30 p-3.5">
              <CodeBlock code={activeCode} className="overflow-x-auto" />
            </div>
            <div className="mt-3 flex items-start gap-2 rounded-lg border border-border bg-muted/30 p-3 text-xs text-muted-foreground">
              <Robot className="mt-0.5 size-3.5 shrink-0" />
              <p>代码由 AI 根据你的自然语言描述生成，运行前建议先在回测预览中核对逻辑与参数。</p>
            </div>
          </TabsContent>
          <TabsContent value="backtest" className="min-h-0 flex-1 overflow-y-auto px-4 pb-4 scrollbar-thin">
            {!metrics && !btRunning && (
              <div className="rounded-lg border border-dashed border-border py-14 text-center text-sm text-muted-foreground">
                点击「运行回测」查看历史表现
              </div>
            )}
            {btRunning && (
              <div className="rounded-lg border border-dashed border-border py-14 text-center text-sm text-muted-foreground">
                正在回测…
              </div>
            )}
            {metrics && (
              <div className="space-y-3">
                <div className="grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-border bg-border">
                  {[
                    { label: "年化收益", value: `${metrics.annualizedReturn > 0 ? "+" : ""}${metrics.annualizedReturn}%`, tone: metrics.annualizedReturn >= 0 ? "up" : "down" },
                    { label: "最大回撤", value: `${metrics.maxDrawdown}%`, tone: "down" as const },
                    { label: "夏普比率", value: `${metrics.sharpe}`, tone: "flat" as const },
                    { label: "胜率", value: `${metrics.winRate}%`, tone: "flat" as const },
                    { label: "命中股票数", value: `${metrics.hitCount}`, tone: "flat" as const },
                  ].map((m) => (
                    <div key={m.label} className="bg-card px-3 py-3">
                      <p className="text-xs text-muted-foreground">{m.label}</p>
                      <p
                        className={cn(
                          "mt-1 font-mono text-base font-semibold tabular-nums",
                          m.tone === "up" && "text-up",
                          m.tone === "down" && "text-down",
                          m.tone === "flat" && "text-foreground",
                        )}
                      >
                        {m.value}
                      </p>
                    </div>
                  ))}
                </div>
                {backtest && <EquityCurve curve={backtest.curve} />}
                <p className="text-xs text-muted-foreground">
                  回测基于历史日线数据，等权持有、按月调仓，费率按双边 0.05% 计算。历史表现不代表未来收益。
                </p>
              </div>
            )}
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}
