import { useEffect, useMemo, useRef, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
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
  Check,
  X,
} from "@phosphor-icons/react"
import { CodeBlock } from "@/components/strategy/code-block"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import {
  fetchStrategies,
  createStrategy,
  updateStrategy,
  deleteStrategy,
  chatStrategy,
  fetchChatHistory,
  saveChatHistory,
  type Strategy,
} from "@/api/strategies"
import { fetchBacktest } from "@/api/backtests"
import { cn } from "@/lib/utils"

type Message = {
  role: "user" | "assistant"
  text: string
  code?: string
  /** AI 本回合产出的策略修改建议——需用户手动应用/忽略，不直接改草稿 */
  proposal?: { dsl: Record<string, unknown>; code: string; name?: string; prompt: string }
  proposalState?: "pending" | "accepted" | "rejected"
}

const PLACEHOLDER_CODE = `# 在左侧用自然语言描述你的选股逻辑，\n# AI 会在这里生成可回测的策略代码。`

const DIAGNOSE_PROMPT =
  "请基于最近一次回测结果，诊断这个策略的主要问题（回撤来源、最差时段/标的的共性），并给出改进后的策略。"

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
  // 草稿相对已保存版本有改动（保存 = 更新该策略，而不是新建）
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  // 最近一次回测的 metrics：从回测页深链带入后，后续追问也一直带着
  const [lastBacktest, setLastBacktest] = useState<Record<string, unknown> | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()

  // messages 的同步镜像：诊断深链等"非输入框触发"的发送需要拿到已提交的最新会话
  const messagesRef = useRef<Message[]>([])
  // savedId 的同步镜像：流式回复结束后的历史落库发生在异步回调里
  const savedIdRef = useRef<string | null>(null)

  // 对话历史全量同步到服务器（幂等 PUT）；未保存的草稿会话不落库，保存时补同步
  function syncChat() {
    const sid = savedIdRef.current
    if (!sid) return
    const items = messagesRef.current.map((m) => ({ role: m.role, text: m.text, code: m.code ?? null }))
    saveChatHistory(sid, items).catch(() => {}) // 静默失败，下一轮对话会再次全量同步
  }

  // 流式输出时跟随滚动到底部；用户正翻看历史（离底部较远）时不打扰
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 120) {
      el.scrollTo({ top: el.scrollHeight })
    }
  }, [messages])
  useEffect(() => {
    messagesRef.current = messages
  }, [messages])
  useEffect(() => {
    savedIdRef.current = savedId
  }, [savedId])

  // 深链 /app/strategy?s=<id>&diagnose=<backtestId>：从回测页跳来，自动发起 AI 诊断
  const diagnoseFired = useRef(false)
  useEffect(() => {
    const sid = searchParams.get("s")
    const bid = searchParams.get("diagnose")
    if (!sid || !bid || strategies.length === 0 || diagnoseFired.current) return
    const s = strategies.find((x) => x.id === sid)
    if (!s) return
    diagnoseFired.current = true
    // loadStrategy 会异步拉对话历史，必须等它完成再发诊断，否则迟到的
    // setMessages(history) 会冲掉进行中的诊断会话
    ;(async () => {
      await loadStrategy(s)
      try {
        const res = await fetchBacktest(Number(bid))
        const metrics = res.metrics as Record<string, unknown>
        setLastBacktest(metrics)
        setSearchParams({}, { replace: true })
        // 等 loadStrategy 的 setMessages 提交后再发起
        requestAnimationFrame(() =>
          sendText(DIAGNOSE_PROMPT, { backtest: metrics, dsl: s.dsl }),
        )
      } catch {
        toast.error("载入回测结果失败")
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategies, searchParams])

  async function send() {
    const text = input.trim()
    if (!text || thinking) return
    setInput("")
    await sendText(text)
  }

  async function sendText(
    text: string,
    extra?: { backtest?: Record<string, unknown> | null; dsl?: Record<string, unknown> | null },
  ) {
    if (!text || thinking) return
    const base = messagesRef.current
    setMessages([...base, { role: "user", text }, { role: "assistant", text: "" }])
    setThinking(true)

    // 本轮之前的会话作为上下文；当前草稿 DSL 一并带上，支持"把 PE 收紧到 20"式增量修改
    const history = base.slice(-12).map((m) => ({ role: m.role, content: m.text }))
    const assistantIndex = base.length + 1

    try {
      await chatStrategy(
        {
          text,
          history,
          currentDsl: extra?.dsl !== undefined ? extra.dsl : draftDsl,
          lastBacktest: extra?.backtest !== undefined ? extra.backtest : lastBacktest,
        },
        (ev) => {
        if (ev.type === "text") {
          setMessages((m) => {
            const next = [...m]
            const cur = next[assistantIndex]
            if (cur) next[assistantIndex] = { ...cur, text: cur.text + ev.delta }
            return next
          })
        } else if (ev.type === "done" && ev.dsl && ev.code) {
          // 产出策略的回合只挂"待确认建议"，等用户手动应用/忽略，不直接改草稿
          const { dsl, code, name } = ev
          setMessages((m) => {
            const next = [...m]
            const cur = next[assistantIndex]
            if (cur)
              next[assistantIndex] = {
                ...cur,
                code,
                proposal: { dsl, code, name, prompt: text },
                proposalState: "pending",
              }
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
        syncChat() // rAF 在 effects 之后触发，此时 messagesRef 已是本轮完整会话
      })
    }
  }

  function acceptProposal(index: number) {
    const msg = messagesRef.current[index]
    if (!msg?.proposal || msg.proposalState !== "pending") return
    const { dsl, code, name, prompt } = msg.proposal
    setDraftDsl(dsl)
    setDraftCode(code)
    if (name) setDraftName(name)
    setLastPrompt(prompt)
    setDirty(true) // 草稿变了；保存时更新当前策略（未关联策略则新建）
    setMessages((m) => m.map((x, i) => (i === index ? { ...x, proposalState: "accepted" as const } : x)))
    toast.success("已应用到策略草稿")
  }

  function rejectProposal(index: number) {
    setMessages((m) => m.map((x, i) => (i === index ? { ...x, proposalState: "rejected" as const } : x)))
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
      const body = {
        name,
        description: lastPrompt,
        tags: ["AI"],
        dsl: draftDsl,
        code: draftCode,
      }
      // 已关联策略 → 原地更新；否则新建
      const saved = savedId ? await updateStrategy(savedId, body) : await createStrategy(body)
      setSavedId(saved.id)
      savedIdRef.current = saved.id
      syncChat() // 首次保存时把此前"无主"的会话补挂到新策略上
      setDirty(false)
      qc.invalidateQueries({ queryKey: ["strategies"] })
      qc.removeQueries({ queryKey: ["strategyHits", saved.id] })
      toast.success(`已保存策略「${saved.name}」`)
      return saved.id
    } catch {
      toast.error("保存失败")
      return null
    } finally {
      setSaving(false)
    }
  }

  async function goBacktest() {
    let id = savedId
    if (!id || dirty) id = await save()
    if (id) navigate(`/app/backtest?s=${id}`)
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
      if (savedId === s.id) {
        setSavedId(null) // 当前载入的被删了，退回未保存草稿
        setDirty(false)
      }
      toast.success(`已删除「${s.name}」`)
    } catch {
      toast.error("删除失败，请重试")
    }
  }

  async function loadStrategy(s: Strategy) {
    setDraftDsl(s.dsl)
    setDraftCode(s.code)
    setDraftName(s.name)
    setSavedId(s.id)
    savedIdRef.current = s.id
    setDirty(false)
    setLastPrompt(s.description)
    try {
      const history = await fetchChatHistory(s.id)
      if (history.length > 0) {
        setMessages(
          history.map((m) => ({ role: m.role, text: m.text, code: m.code ?? undefined })),
        )
        return
      }
    } catch {
      // 拉历史失败不阻塞载入，退回合成开场白
    }
    // 没有落库历史的老策略：合成开场白
    setMessages([
      { role: "user", text: s.description || s.name },
      { role: "assistant", text: `已载入策略「${s.name}」，可继续对话调整，或去回测页检验表现。`, code: s.code },
    ])
  }

  const activeCode = draftCode || PLACEHOLDER_CODE

  return (
    <div className="grid h-[calc(100dvh-4rem)] grid-cols-1 lg:grid-cols-[240px_1fr_440px]">
      {/* Strategy list */}
      <aside className="hidden min-h-0 border-r border-border lg:flex lg:flex-col">
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
              setDirty(false)
            }}
          >
            <Plus className="size-4" />
          </Button>
        </div>
        <div className="min-h-0 flex-1 space-y-1 overflow-y-auto px-2 pb-3 scrollbar-thin">
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
      <div className="flex min-h-0 min-w-0 flex-col border-r border-border">
        <div className="flex h-12 shrink-0 items-center px-4">
          <p className="text-sm font-medium text-foreground">策略工坊</p>
        </div>
        {/* min-h-0 让长对话在本栏内滚动，输入框常驻底部，不再把整页撑出滚动条 */}
        <div ref={scrollRef} className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4 scrollbar-thin">
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
                {m.proposal &&
                  (m.proposalState === "pending" ? (
                    <div className="flex flex-wrap items-center gap-2">
                      <Button
                        size="sm"
                        className="h-7 gap-1 px-2.5 text-xs"
                        onClick={() => acceptProposal(i)}
                      >
                        <Check className="size-3.5" />
                        应用到草稿
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-7 gap-1 px-2.5 text-xs"
                        onClick={() => rejectProposal(i)}
                      >
                        <X className="size-3.5" />
                        忽略
                      </Button>
                      <span className="text-[11px] text-muted-foreground">
                        应用后将替换右侧策略草稿
                      </span>
                    </div>
                  ) : (
                    <p className="text-[11px] text-muted-foreground">
                      {m.proposalState === "accepted" ? "✓ 已应用到草稿" : "已忽略此建议"}
                    </p>
                  ))}
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

      {/* Code panel */}
      <div className="flex min-h-0 min-w-0 flex-col">
        <div className="flex h-12 shrink-0 items-center justify-between px-4">
          <p className="text-sm font-medium text-foreground">策略代码</p>
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
            <Button size="sm" className="gap-1.5" onClick={goBacktest} disabled={saving || !draftDsl}>
              <Play weight="fill" className="size-3.5" />
              去回测
            </Button>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4 scrollbar-thin">
          <div className="rounded-lg border border-border bg-muted/30 p-3.5">
            <CodeBlock code={activeCode} className="overflow-x-auto" />
          </div>
          <div className="mt-3 flex items-start gap-2 rounded-lg border border-border bg-muted/30 p-3 text-xs text-muted-foreground">
            <Robot className="mt-0.5 size-3.5 shrink-0" />
            <p>代码由 AI 根据你的自然语言描述生成，保存后可在回测页检验历史表现。</p>
          </div>
        </div>
      </div>
    </div>
  )
}
