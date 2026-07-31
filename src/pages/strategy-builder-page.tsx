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
import {
  submitBacktest,
  fetchBacktest,
  type BacktestParams,
  type BacktestResult,
} from "@/api/backtests"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"

type Message = { role: "user" | "assistant"; text: string; code?: string }

const PLACEHOLDER_CODE = `# 在左侧用自然语言描述你的选股逻辑，\n# AI 会在这里生成可回测的策略代码。`

function EquityCurve({
  curve,
  benchmark = [],
}: {
  curve: { t: string; v: number }[]
  benchmark?: { t: string; v: number }[]
}) {
  if (curve.length < 2) return null
  const all = [...curve.map((p) => p.v), ...benchmark.map((p) => p.v)]
  const min = Math.min(...all)
  const max = Math.max(...all)
  const range = max - min || 1
  const w = 100
  const h = 40
  const toPts = (series: { v: number }[]) =>
    series
      .map((p, i) => {
        const x = (i / (series.length - 1)) * w
        const y = h - ((p.v - min) / range) * h
        return `${x.toFixed(2)},${y.toFixed(2)}`
      })
      .join(" ")
  const up = curve[curve.length - 1].v >= curve[0].v
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="h-24 w-full">
      {benchmark.length > 1 && (
        <polyline
          points={toPts(benchmark)}
          fill="none"
          stroke="var(--color-muted-foreground, #9ca3af)"
          strokeWidth="1"
          strokeDasharray="2 2"
          opacity="0.6"
          vectorEffect="non-scaling-stroke"
        />
      )}
      <polyline
        points={toPts(curve)}
        fill="none"
        stroke={up ? "var(--color-up, #16a34a)" : "var(--color-down, #dc2626)"}
        strokeWidth="1"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  )
}

function ParamSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  options: [string, string][]
}) {
  const labels = Object.fromEntries(options)
  return (
    <label className="flex items-center gap-1.5">
      <span className="whitespace-nowrap text-xs text-muted-foreground">{label}</span>
      <Select value={value} onValueChange={(v) => onChange(v as string)}>
        <SelectTrigger size="sm" className="text-xs">
          <SelectValue>{(v: string) => labels[v] ?? v}</SelectValue>
        </SelectTrigger>
        <SelectContent alignItemWithTrigger={false}>
          {options.map(([v, l]) => (
            <SelectItem key={v} value={v}>
              {l}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </label>
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
  const [draftName, setDraftName] = useState<string>("")
  const [lastPrompt, setLastPrompt] = useState("")
  const [savedId, setSavedId] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [backtest, setBacktest] = useState<BacktestResult | null>(null)
  const [btRunning, setBtRunning] = useState(false)
  const [btParams, setBtParams] = useState<BacktestParams>({
    periodDays: 250,
    holdDays: 10,
    entry: "open",
    exitRule: "hold",
    stopGain: 15,
    stopLoss: 8,
    rebalance: "monthly",
    costRate: 0.0005,
    benchmark: "000300",
    weighting: "equal",
    maxPositions: 0,
  })
  const [tab, setTab] = useState("code")
  const scrollRef = useRef<HTMLDivElement>(null)

  // 含技术形态的策略走事件驱动回测，参数面板按模式显隐
  const isEventStrategy =
    Array.isArray((draftDsl as { technical?: unknown[] } | null)?.technical) &&
    ((draftDsl as { technical: unknown[] }).technical.length > 0)

  function setParam<K extends keyof BacktestParams>(key: K, value: BacktestParams[K]) {
    setBtParams((p) => ({ ...p, [key]: value }))
  }

  async function send() {
    const text = input.trim()
    if (!text || thinking) return
    setInput("")
    setMessages((m) => [...m, { role: "user", text }])
    setThinking(true)

    // 本轮之前的会话作为上下文；当前草稿 DSL 一并带上，支持"把 PE 收紧到 20"式增量修改
    const history = messages.slice(-12).map((m) => ({ role: m.role, content: m.text }))
    const assistantIndex = messages.length + 1
    setMessages((m) => [...m, { role: "assistant", text: "" }])

    try {
      await chatStrategy({ text, history, currentDsl: draftDsl }, (ev) => {
        if (ev.type === "text") {
          setMessages((m) => {
            const next = [...m]
            const cur = next[assistantIndex]
            if (cur) next[assistantIndex] = { ...cur, text: cur.text + ev.delta }
            return next
          })
        } else if (ev.type === "done" && ev.dsl && ev.code) {
          // 只有产出策略的回合才更新右侧面板；纯闲聊不动当前草稿
          const { dsl, code } = ev
          setDraftDsl(dsl)
          setDraftCode(code)
          if (ev.name) setDraftName(ev.name)
          setLastPrompt(text)
          setSavedId(null)
          setBacktest(null)
          setMessages((m) => {
            const next = [...m]
            const cur = next[assistantIndex]
            if (cur) next[assistantIndex] = { ...cur, code }
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
      // AI 起的标题优先；降级路径（规则解析）没有标题时退回截断的用户描述
      const name = draftName || lastPrompt.slice(0, 16) || "未命名策略"
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
      const { id: bid } = await submitBacktest(id, btParams)
      for (let i = 0; i < 120; i++) {
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
    setDraftName(s.name)
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
              setDraftName("")
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
            <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-border bg-muted/30 p-3">
              <ParamSelect
                label="区间"
                value={String(btParams.periodDays)}
                onChange={(v) => setParam("periodDays", Number(v))}
                options={[["120", "近半年"], ["250", "近1年"], ["500", "近2年"], ["750", "近3年"]]}
              />
              <ParamSelect
                label="费率"
                value={String(btParams.costRate)}
                onChange={(v) => setParam("costRate", Number(v))}
                options={[["0.0005", "万5"], ["0.001", "千1"], ["0.002", "千2"]]}
              />
              <ParamSelect
                label="基准"
                value={btParams.benchmark!}
                onChange={(v) => setParam("benchmark", v as BacktestParams["benchmark"])}
                options={[["000300", "沪深300"], ["000905", "中证500"], ["399006", "创业板指"]]}
              />
              {isEventStrategy ? (
                <>
                  <ParamSelect
                    label="持有"
                    value={String(btParams.holdDays)}
                    onChange={(v) => setParam("holdDays", Number(v))}
                    options={[["5", "5天"], ["10", "10天"], ["20", "20天"], ["30", "30天"]]}
                  />
                  <ParamSelect
                    label="入场"
                    value={btParams.entry!}
                    onChange={(v) => setParam("entry", v as BacktestParams["entry"])}
                    options={[["open", "次日开盘"], ["close", "次日收盘"]]}
                  />
                  <ParamSelect
                    label="退出"
                    value={btParams.exitRule!}
                    onChange={(v) => setParam("exitRule", v as BacktestParams["exitRule"])}
                    options={[["hold", "持有到期"], ["signal", "反向信号"], ["stop", "止盈止损"]]}
                  />
                  {btParams.exitRule === "stop" && (
                    <ParamSelect
                      label="止盈/损"
                      value={`${btParams.stopGain}/${btParams.stopLoss}`}
                      onChange={(v) => {
                        const [g, l] = v.split("/").map(Number)
                        setBtParams((p) => ({ ...p, stopGain: g, stopLoss: l }))
                      }}
                      options={[["10/5", "+10%/-5%"], ["15/8", "+15%/-8%"], ["20/10", "+20%/-10%"], ["30/15", "+30%/-15%"]]}
                    />
                  )}
                </>
              ) : (
                <>
                  <ParamSelect
                    label="调仓"
                    value={btParams.rebalance!}
                    onChange={(v) => setParam("rebalance", v as BacktestParams["rebalance"])}
                    options={[["weekly", "每周"], ["monthly", "每月"], ["quarterly", "每季"]]}
                  />
                  <ParamSelect
                    label="权重"
                    value={btParams.weighting!}
                    onChange={(v) => setParam("weighting", v as BacktestParams["weighting"])}
                    options={[["equal", "等权"], ["cap", "市值加权"]]}
                  />
                  <ParamSelect
                    label="持仓数"
                    value={String(btParams.maxPositions)}
                    onChange={(v) => setParam("maxPositions", Number(v))}
                    options={[["0", "不限"], ["10", "前10"], ["20", "前20"], ["50", "前50"]]}
                  />
                </>
              )}
            </div>
            {!metrics && !btRunning && (
              <div className="rounded-lg border border-dashed border-border py-14 text-center text-sm text-muted-foreground">
                调好参数后点击「运行回测」查看历史表现
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
                  {(metrics.mode === "event"
                    ? [
                        { label: "信号次数", value: `${metrics.eventCount}`, tone: "flat" as const },
                        { label: "胜率", value: `${metrics.winRate}%`, tone: "flat" as const },
                        { label: "平均单次收益", value: `${(metrics.avgReturn ?? 0) > 0 ? "+" : ""}${metrics.avgReturn}%`, tone: (metrics.avgReturn ?? 0) >= 0 ? ("up" as const) : ("down" as const) },
                        { label: "中位收益", value: `${metrics.medianReturn}%`, tone: "flat" as const },
                        { label: "平均持有", value: `${metrics.avgHoldDays} 天`, tone: "flat" as const },
                        { label: `超额 vs ${metrics.benchmarkName ?? "基准"}`, value: metrics.avgExcess == null ? "—" : `${metrics.avgExcess > 0 ? "+" : ""}${metrics.avgExcess}%`, tone: (metrics.avgExcess ?? 0) >= 0 ? ("up" as const) : ("down" as const) },
                      ]
                    : [
                        { label: "年化收益", value: `${(metrics.annualizedReturn ?? 0) > 0 ? "+" : ""}${metrics.annualizedReturn}%`, tone: (metrics.annualizedReturn ?? 0) >= 0 ? ("up" as const) : ("down" as const) },
                        { label: "最大回撤", value: `${metrics.maxDrawdown}%`, tone: "down" as const },
                        { label: "夏普比率", value: `${metrics.sharpe}`, tone: "flat" as const },
                        { label: "胜率", value: `${metrics.winRate}%`, tone: "flat" as const },
                        { label: `${metrics.benchmarkName ?? "基准"}同期`, value: metrics.benchmarkReturn == null ? "—" : `${metrics.benchmarkReturn > 0 ? "+" : ""}${metrics.benchmarkReturn}%`, tone: "flat" as const },
                        { label: "超额收益", value: metrics.excessReturn == null ? "—" : `${metrics.excessReturn > 0 ? "+" : ""}${metrics.excessReturn}%`, tone: (metrics.excessReturn ?? 0) >= 0 ? ("up" as const) : ("down" as const) },
                      ]
                  ).map((m) => (
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
                {backtest && (
                  <EquityCurve curve={backtest.curve} benchmark={backtest.benchmark} />
                )}
                <p className="text-xs text-muted-foreground">
                  {metrics.mode === "event"
                    ? "事件驱动回测：信号次日入场、按所选规则退出，同一股票同时只持一笔；曲线为全部信号的平均收益路径。成分按当前条件筛选，存在一定前视偏差。"
                    : `组合回测：每个调仓期按当期时点数据重新选股（虚线为基准指数）。时点因子来自每日收盘快照${
                        metrics.rebalances
                          ? `，本次 ${metrics.rebalances} 期中 ${metrics.pitPeriods ?? 0} 期有真实快照`
                          : ""
                      }；快照未覆盖的日期用价格重构近似（ROE/股息率/行业按当前值）。历史表现不代表未来收益。`}
                </p>
              </div>
            )}
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}
