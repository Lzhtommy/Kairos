import { useState } from "react"
import {
  Sparkle,
  PaperPlaneRight,
  Play,
  FloppyDisk,
  Plus,
  Robot,
} from "@phosphor-icons/react"
import { STRATEGIES } from "@/lib/mock-data"
import { CodeBlock } from "@/components/strategy/code-block"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { cn } from "@/lib/utils"

type Message = {
  role: "user" | "assistant"
  text: string
  code?: string
}

const GENERATED_CODE = `def screen(stock):
    """低估值高股息策略"""
    pe_ok = stock.pe is not None and stock.pe < stock.industry_median_pe
    dividend_ok = stock.dividend_yield >= 0.03
    roe_stable = min(stock.roe_last_3y) >= 0.12

    return pe_ok and dividend_ok and roe_stable

# 回测区间: 2021-01-01 至今
# 调仓频率: 每月第一个交易日
`

const INITIAL_MESSAGES: Message[] = [
  {
    role: "user",
    text: "帮我写一个策略：市盈率低于行业中位数，股息率不低于3%，并且过去三年ROE都在12%以上",
  },
  {
    role: "assistant",
    text: "明白，这是一个偏防御的低估值高股息策略。我按下面逻辑生成了代码：PE 低于行业中位数作为估值过滤，股息率≥3%保证现金回报，近三年ROE最低值≥12%保证盈利质量的稳定性，而不是单一年份达标。",
    code: GENERATED_CODE,
  },
]

export function StrategyBuilderPage() {
  const [messages, setMessages] = useState<Message[]>(INITIAL_MESSAGES)
  const [input, setInput] = useState("")
  const [selectedStrategy, setSelectedStrategy] = useState(STRATEGIES[0].id)
  const [thinking, setThinking] = useState(false)

  function send() {
    const text = input.trim()
    if (!text) return
    setMessages((m) => [...m, { role: "user", text }])
    setInput("")
    setThinking(true)
    setTimeout(() => {
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          text: "已根据你的描述更新策略逻辑，代码见右侧面板，你可以点击“运行回测”查看历史表现。",
          code: GENERATED_CODE,
        },
      ])
      setThinking(false)
    }, 1200)
  }

  return (
    <div className="grid h-[calc(100dvh-4rem)] grid-cols-1 lg:grid-cols-[240px_1fr_440px]">
      {/* Strategy list */}
      <aside className="hidden border-r border-border lg:flex lg:flex-col">
        <div className="flex items-center justify-between p-3">
          <span className="text-sm font-medium text-foreground">我的策略</span>
          <Button size="icon-sm" variant="ghost" aria-label="新建策略">
            <Plus className="size-4" />
          </Button>
        </div>
        <div className="flex-1 space-y-1 overflow-y-auto px-2 pb-3 scrollbar-thin">
          {STRATEGIES.map((s) => (
            <button
              key={s.id}
              onClick={() => setSelectedStrategy(s.id)}
              className={cn(
                "w-full rounded-md px-2.5 py-2 text-left transition-colors",
                selectedStrategy === s.id
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
              )}
            >
              <p className="truncate text-sm font-medium">{s.name}</p>
              <p className="mt-0.5 truncate text-xs opacity-70">{s.createdAt}</p>
            </button>
          ))}
        </div>
      </aside>

      {/* Chat */}
      <div className="flex min-w-0 flex-col border-r border-border">
        <div className="flex h-12 shrink-0 items-center px-4">
          <p className="text-sm font-medium text-foreground">低估值高股息</p>
        </div>
        <div className="flex-1 space-y-5 overflow-y-auto px-4 py-4 scrollbar-thin">
          {messages.map((m, i) => (
            <div key={i} className={cn("flex gap-3", m.role === "user" && "flex-row-reverse")}>
              {m.role === "assistant" ? (
                <Avatar className="size-7 shrink-0">
                  <AvatarFallback className="bg-primary/15 text-primary">
                    <Sparkle weight="fill" className="size-3.5" />
                  </AvatarFallback>
                </Avatar>
              ) : (
                <Avatar className="size-7 shrink-0">
                  <AvatarFallback className="bg-muted text-muted-foreground text-xs">陆</AvatarFallback>
                </Avatar>
              )}
              <div
                className={cn(
                  "max-w-[85%] space-y-3 rounded-xl px-3.5 py-2.5 text-sm leading-relaxed",
                  m.role === "user"
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-foreground",
                )}
              >
                <p>{m.text}</p>
                {m.code && (
                  <div className="-mx-1 rounded-lg border border-border/60 bg-background/60 p-3">
                    <CodeBlock code={m.code} className="overflow-x-auto" />
                  </div>
                )}
              </div>
            </div>
          ))}
          {thinking && (
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
            <Button size="icon" onClick={send} aria-label="发送" disabled={!input.trim()}>
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
        <Tabs defaultValue="code" className="flex min-h-0 flex-1 flex-col">
          <div className="flex h-12 shrink-0 items-center justify-between px-4">
            <TabsList>
              <TabsTrigger value="code">策略代码</TabsTrigger>
              <TabsTrigger value="backtest">回测预览</TabsTrigger>
            </TabsList>
            <div className="flex gap-1.5">
              <Button size="icon-sm" variant="ghost" aria-label="保存策略">
                <FloppyDisk className="size-4" />
              </Button>
              <Button size="sm" className="gap-1.5">
                <Play weight="fill" className="size-3.5" />
                运行回测
              </Button>
            </div>
          </div>
          <TabsContent value="code" className="min-h-0 flex-1 overflow-y-auto px-4 pb-4 scrollbar-thin">
            <div className="rounded-lg border border-border bg-muted/30 p-3.5">
              <CodeBlock code={GENERATED_CODE} className="overflow-x-auto" />
            </div>
            <div className="mt-3 flex items-start gap-2 rounded-lg border border-border bg-muted/30 p-3 text-xs text-muted-foreground">
              <Robot className="mt-0.5 size-3.5 shrink-0" />
              <p>代码由 AI 根据你的自然语言描述生成，运行前建议先在回测预览中核对逻辑与参数。</p>
            </div>
          </TabsContent>
          <TabsContent value="backtest" className="min-h-0 flex-1 overflow-y-auto px-4 pb-4 scrollbar-thin">
            <div className="space-y-3">
              <div className="grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-border bg-border">
                {[
                  { label: "年化收益", value: "+18.4%", tone: "up" as const },
                  { label: "最大回撤", value: "-12.7%", tone: "down" as const },
                  { label: "命中股票数", value: "34", tone: "flat" as const },
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
              <p className="text-xs text-muted-foreground">
                回测区间 2021-01-01 至今，样本为全部 A 股剔除 ST，费率按双边 0.05% 计算。历史表现不代表未来收益。
              </p>
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}
