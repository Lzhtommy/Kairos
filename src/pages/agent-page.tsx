import { Fragment, useEffect, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Brain, CaretDown, CaretRight, Plus, Play, Robot, Trash } from "@phosphor-icons/react"
import { BacktestChart } from "@/components/strategy/backtest-chart"
import { Markdown } from "@/components/research/research-ui"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useAuth } from "@/lib/auth"
import { cn } from "@/lib/utils"
import {
  createAgentAccount,
  deleteAgentAccount,
  fetchAgentAccount,
  fetchAgentAccounts,
  fetchAgentSession,
  fetchAgentSessions,
  fetchPlaybooks,
  patchAgentAccount,
  runAgent,
  type AgentDetail,
  type AgentOrder,
  type SessionKind,
} from "@/api/agent"

const BENCH_NAMES: Record<string, string> = {
  "000300": "沪深300",
  "000905": "中证500",
  "000852": "中证1000",
  "399006": "创业板指",
}

const ORDER_STATUS: Record<AgentOrder["status"], string> = {
  pending: "待撮合",
  filled: "成交",
  expired: "未成交",
  rejected: "拒单",
  cancelled: "已撤",
}

const MEMORY_KIND: Record<string, string> = { lesson: "教训", rule: "规则", watch: "观察" }

const RUN_KINDS: { kind: SessionKind; label: string; hint: string }[] = [
  { kind: "manual", label: "立即分析", hint: "检查持仓与市场，必要时下单（09:30 后下的单次日撮合）" },
  { kind: "postclose", label: "盘后复盘", hint: "先结算到最新交易日，再复盘并沉淀经验" },
  { kind: "review", label: "周度反思", hint: "评估经验库、改写交易手册" },
]

type Tab = "positions" | "journal" | "orders" | "memories" | "playbook"

const fmtMoney = (v: number) => v.toLocaleString("zh-CN", { maximumFractionDigits: 0 })
const fmtPct = (v: number | null | undefined) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`)
const tone = (v: number | null | undefined) => (!v ? undefined : v > 0 ? "text-up" : "text-down")

/** agent 输出的 Markdown 带标题层级，共享的 Markdown 组件没给标题样式，这里补上 */
function AgentMarkdown({ children }: { children: string }) {
  return (
    <div className="[&_h1]:text-base [&_h1]:font-semibold [&_h1]:text-foreground [&_h2]:mt-5 [&_h2]:text-[15px] [&_h2]:font-semibold [&_h2]:text-foreground [&_h3]:mt-4 [&_h3]:text-sm [&_h3]:font-semibold [&_h3]:text-foreground">
      <Markdown>{children}</Markdown>
    </div>
  )
}

function Stat({ label, value, sub, className }: { label: string; value: string; sub?: string; className?: string }) {
  return (
    <div className="rounded-lg border border-border p-3.5">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={cn("mt-1 font-mono text-lg font-semibold tabular-nums", className)}>{value}</p>
      {sub && <p className="mt-0.5 text-[11px] text-muted-foreground">{sub}</p>}
    </div>
  )
}

export function AgentPage() {
  const { user } = useAuth()
  const isAdmin = !!user?.is_admin
  const qc = useQueryClient()
  const listQ = useQuery({ queryKey: ["agentAccounts"], queryFn: fetchAgentAccounts, refetchInterval: 30_000 })
  const items = listQ.data?.items ?? []
  const [selected, setSelected] = useState<number | null>(null)
  const active = selected ?? items[0]?.id ?? null
  const [createOpen, setCreateOpen] = useState(false)

  const detailQ = useQuery({
    queryKey: ["agentAccount", active],
    queryFn: () => fetchAgentAccount(active!),
    enabled: active != null,
    refetchInterval: (q) => (q.state.data?.running ? 5_000 : 60_000),
  })
  const detail = detailQ.data

  return (
    <div className="mx-auto max-w-[1600px] px-4 py-6 lg:px-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-foreground">AI 操盘</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            LLM 自主调研、下单的 A 股模拟账户：每个交易日盘前分析、盘后复盘，按真实费率与撮合规则记账，
            并从实盘结果中沉淀经验、每周改写自己的交易手册
          </p>
        </div>
        {isAdmin && (
          <Button size="sm" className="gap-1.5" onClick={() => setCreateOpen(true)}>
            <Plus className="size-3.5" />
            新建账户
          </Button>
        )}
      </div>

      {listQ.data && !listQ.data.llmEnabled && (
        <p className="mt-4 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-2.5 text-xs text-amber-700 dark:text-amber-400">
          服务端未配置 LLM（AI_PROVIDER / LLM_API_KEY），账户只会按日结算已有挂单，不会运行分析。
        </p>
      )}

      {items.length > 1 && (
        <div className="mt-5 flex flex-wrap gap-2">
          {items.map((a) => (
            <button
              key={a.id}
              onClick={() => setSelected(a.id)}
              className={cn(
                "rounded-lg border px-3 py-2 text-left transition-colors",
                active === a.id ? "border-primary/50 bg-accent/40" : "border-border hover:bg-muted/40",
              )}
            >
              <p className="text-sm font-medium text-foreground">
                {a.name}
                {!a.enabled && <span className="ml-1.5 text-xs text-muted-foreground">已暂停</span>}
              </p>
              <p className={cn("font-mono text-xs tabular-nums", tone(a.returnPct))}>{fmtPct(a.returnPct)}</p>
            </button>
          ))}
        </div>
      )}

      {items.length === 0 && !listQ.isLoading ? (
        <div className="mt-5 flex h-64 flex-col items-center justify-center rounded-lg border border-dashed border-border text-center">
          <Robot className="size-8 text-muted-foreground/60" />
          <p className="mt-3 text-sm text-muted-foreground">
            {isAdmin ? "还没有 AI 账户，点右上角新建一个" : "还没有 AI 账户"}
          </p>
        </div>
      ) : detail ? (
        <AccountView
          detail={detail}
          isAdmin={isAdmin}
          onChanged={() => {
            qc.invalidateQueries({ queryKey: ["agentAccounts"] })
            qc.invalidateQueries({ queryKey: ["agentAccount", detail.id] })
            qc.invalidateQueries({ queryKey: ["agentSessions", detail.id] })
          }}
          onDeleted={() => {
            setSelected(null)
            qc.invalidateQueries({ queryKey: ["agentAccounts"] })
          }}
        />
      ) : null}

      <CreateDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(id) => {
          setSelected(id)
          qc.invalidateQueries({ queryKey: ["agentAccounts"] })
        }}
      />
    </div>
  )
}

function AccountView({
  detail,
  isAdmin,
  onChanged,
  onDeleted,
}: {
  detail: AgentDetail
  isAdmin: boolean
  onChanged: () => void
  onDeleted: () => void
}) {
  const [tab, setTab] = useState<Tab>("journal")
  const [confirmDelete, setConfirmDelete] = useState(false)
  const p = detail.portfolio
  const excess = detail.benchReturnPct != null ? detail.returnPct - detail.benchReturnPct : null
  const bench = BENCH_NAMES[detail.benchmark] ?? detail.benchmark

  async function run(kind: SessionKind) {
    try {
      await runAgent(detail.id, kind)
      toast.success("已开始运行，可在决策日志里实时查看")
      setTab("journal")
      onChanged()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "启动失败")
    }
  }

  async function toggle(enabled: boolean) {
    try {
      await patchAgentAccount(detail.id, { enabled })
      toast.success(enabled ? "已启用自动运行" : "已暂停自动运行（结算与挂单也暂停）")
      onChanged()
    } catch {
      toast.error("操作失败")
    }
  }

  async function remove() {
    try {
      await deleteAgentAccount(detail.id)
      toast.success("已删除")
      setConfirmDelete(false)
      onDeleted()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "删除失败")
    }
  }

  return (
    <div className="mt-5 space-y-5">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="总资产" value={fmtMoney(detail.total)} sub={`初始 ${fmtMoney(detail.initialCash)}`} />
        <Stat label="累计收益" value={fmtPct(detail.returnPct)} className={tone(detail.returnPct)} />
        <Stat
          label={`超额（vs ${bench}）`}
          value={fmtPct(excess)}
          className={tone(excess)}
          sub={detail.benchReturnPct != null ? `基准 ${fmtPct(detail.benchReturnPct)}` : undefined}
        />
        <Stat
          label="仓位"
          value={p.total ? `${((p.marketValue / p.total) * 100).toFixed(0)}%` : "—"}
          sub={`现金 ${fmtMoney(p.cash)}`}
        />
        <Stat
          label="平仓胜率"
          value={detail.stats.winRate != null ? `${detail.stats.winRate}%` : "—"}
          sub={`已平仓 ${detail.stats.closedTrades} 笔`}
        />
        <Stat
          label="最大回撤"
          value={detail.stats.sinceInception ? `${detail.stats.sinceInception.maxDrawdownPct.toFixed(2)}%` : "—"}
          sub={`累计费用 ${fmtMoney(detail.stats.totalFees)} 元`}
        />
      </div>

      {isAdmin && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border px-4 py-2.5">
          {RUN_KINDS.map((r) => (
            <Button
              key={r.kind}
              size="sm"
              variant="outline"
              className="gap-1.5"
              title={r.hint}
              disabled={detail.running}
              onClick={() => run(r.kind)}
            >
              <Play className="size-3" />
              {r.label}
            </Button>
          ))}
          {detail.running && <span className="text-xs text-primary">运行中…</span>}
          <div className="ml-auto flex items-center gap-3">
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              自动运行
              <Switch checked={detail.enabled} onCheckedChange={(v: boolean) => toggle(v)} />
            </label>
            <Button size="sm" variant="ghost" className="text-muted-foreground" onClick={() => setConfirmDelete(true)}>
              <Trash className="size-3.5" />
            </Button>
          </div>
        </div>
      )}

      {detail.curve.length > 1 ? (
        <div className="rounded-lg border border-border p-4">
          <BacktestChart curve={detail.curve} benchmark={detail.benchCurve} benchmarkName={bench} />
        </div>
      ) : (
        <p className="rounded-lg border border-border p-4 text-xs text-muted-foreground">
          {detail.lastSettled ? `已结算至 ${detail.lastSettled}，净值曲线将随逐日结算展开` : "等待首个交易日结算"}
        </p>
      )}

      <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
        <TabsList>
          <TabsTrigger value="journal">决策日志</TabsTrigger>
          <TabsTrigger value="positions">持仓 · {p.positions.length}</TabsTrigger>
          <TabsTrigger value="orders">订单</TabsTrigger>
          <TabsTrigger value="memories">
            经验库 · {detail.memories.filter((m) => m.active).length}
          </TabsTrigger>
          <TabsTrigger value="playbook">交易手册 v{detail.playbookVersion}</TabsTrigger>
        </TabsList>
      </Tabs>

      {tab === "journal" && <Journal accountId={detail.id} running={detail.running} />}
      {tab === "positions" && <Positions detail={detail} />}
      {tab === "orders" && <Orders orders={detail.orders} />}
      {tab === "memories" && <Memories detail={detail} />}
      {tab === "playbook" && <PlaybookView accountId={detail.id} current={detail.playbook} />}

      <p className="text-[11px] leading-relaxed text-muted-foreground">
        撮合规则：09:30 前下的单按当日行情、之后的单按下一个交易日撮合；市价单开盘价成交（滑点{" "}
        {detail.fees.slippageBps} bp），限价单触及才成交，当日未成交作废；T+1，100 股一手（科创板 200 股起），
        一字涨停买不进、一字跌停卖不出，单笔不超过当日成交量 5%。费用：佣金万
        {(detail.fees.commission * 10000).toFixed(1)}（最低 {detail.fees.minCommission} 元）、卖出印花税千
        {(detail.fees.stampDuty * 1000).toFixed(1)}、过户费十万分之{(detail.fees.transferFee * 100000).toFixed(0)}
        。除权除息按分红方案入账（差别化红利税）。
      </p>

      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>删除 AI 账户</DialogTitle>
            <DialogDescription>
              将永久删除「{detail.name}」的持仓、订单、净值、决策日志、经验库与手册历史，无法恢复。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmDelete(false)}>
              取消
            </Button>
            <Button variant="destructive" onClick={remove}>
              确认删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function Journal({ accountId, running }: { accountId: number; running: boolean }) {
  const listQ = useQuery({
    queryKey: ["agentSessions", accountId],
    queryFn: () => fetchAgentSessions(accountId),
    refetchInterval: running ? 5_000 : false,
  })
  const sessions = listQ.data ?? []
  const [picked, setPicked] = useState<number | null>(null)
  const current = picked ?? sessions[0]?.id ?? null
  const currentRow = sessions.find((s) => s.id === current)
  const sessionQ = useQuery({
    queryKey: ["agentSession", current],
    queryFn: () => fetchAgentSession(current!),
    enabled: current != null,
    refetchInterval: currentRow?.status === "running" ? 4_000 : false,
  })
  const s = sessionQ.data
  const [showTrace, setShowTrace] = useState(false)
  useEffect(() => setShowTrace(false), [current])

  if (sessions.length === 0) {
    return <p className="rounded-lg border border-border p-4 text-xs text-muted-foreground">还没有运行记录</p>
  }
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[280px_1fr]">
      <div className="max-h-[720px] space-y-1.5 overflow-y-auto pr-1">
        {sessions.map((x) => (
          <button
            key={x.id}
            onClick={() => setPicked(x.id)}
            className={cn(
              "w-full rounded-lg border px-3 py-2 text-left transition-colors",
              current === x.id ? "border-primary/50 bg-accent/40" : "border-border hover:bg-muted/40",
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-medium text-foreground">{x.kindLabel}</span>
              <span
                className={cn(
                  "text-[11px]",
                  x.status === "failed" ? "text-down" : x.status === "running" ? "text-primary" : "text-muted-foreground",
                )}
              >
                {x.status === "running" ? "运行中" : x.status === "failed" ? "失败" : x.tradeDate}
              </span>
            </div>
            <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{x.error ?? x.preview ?? ""}</p>
          </button>
        ))}
      </div>

      <div className="min-w-0 rounded-lg border border-border p-5">
        {!s ? (
          <p className="text-xs text-muted-foreground">加载中…</p>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <h3 className="text-base font-semibold text-foreground">
                {s.tradeDate} {s.kindLabel}
              </h3>
              <span className="text-xs text-muted-foreground">
                {s.steps} 次工具调用 · {(s.tokens / 1000).toFixed(0)}k tokens{s.model && ` · ${s.model}`}
              </span>
            </div>
            {s.status === "failed" && (
              <p className="rounded-md bg-down/10 px-3 py-2 text-xs text-down">运行失败：{s.error}</p>
            )}
            {s.status === "running" && (
              <p className="text-xs text-primary">运行中，已调用 {s.trace.length} 次工具…</p>
            )}
            {s.summary && <AgentMarkdown>{s.summary}</AgentMarkdown>}

            {s.orders.length > 0 && (
              <div>
                <p className="mb-2 text-sm font-medium text-foreground">本次下单</p>
                <OrderTable orders={s.orders} compact />
              </div>
            )}

            {s.trace.length > 0 && (
              <div>
                <button
                  className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                  onClick={() => setShowTrace((v) => !v)}
                >
                  {showTrace ? <CaretDown className="size-3" /> : <CaretRight className="size-3" />}
                  工具调用轨迹（{s.trace.length}）
                </button>
                {showTrace && (
                  <ol className="mt-2 space-y-1.5">
                    {s.trace.map((t, i) => (
                      <li key={i} className="rounded-md bg-muted/40 px-3 py-2 font-mono text-[11px] leading-relaxed">
                        <span className="text-primary">{t.tool}</span>
                        <span className="text-muted-foreground">({JSON.stringify(t.args)})</span>
                        <p className="mt-0.5 break-all text-muted-foreground/80">{t.result}</p>
                      </li>
                    ))}
                  </ol>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function Positions({ detail }: { detail: AgentDetail }) {
  const rows = detail.portfolio.positions
  const pending = detail.orders.filter((o) => o.status === "pending")
  return (
    <div className="space-y-4">
      <div className="overflow-x-auto rounded-lg border border-border">
        {rows.length === 0 ? (
          <p className="px-4 py-3 text-xs text-muted-foreground">空仓</p>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border text-muted-foreground">
                <th className="px-4 py-2 text-left font-normal">股票</th>
                <th className="px-2 py-2 text-right font-normal">股数</th>
                <th className="px-2 py-2 text-right font-normal">成本价</th>
                <th className="px-2 py-2 text-right font-normal">现价</th>
                <th className="px-2 py-2 text-right font-normal">市值</th>
                <th className="px-2 py-2 text-right font-normal">占比</th>
                <th className="px-2 py-2 text-right font-normal">浮动盈亏</th>
                <th className="px-4 py-2 text-right font-normal">建仓日</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.code} className="border-b border-border/60 last:border-0">
                  <td className="px-4 py-2">
                    {r.name} <span className="font-mono text-muted-foreground">{r.code}</span>
                  </td>
                  <td className="px-2 py-2 text-right font-mono tabular-nums">{r.shares}</td>
                  <td className="px-2 py-2 text-right font-mono tabular-nums">{r.avgCost.toFixed(3)}</td>
                  <td className="px-2 py-2 text-right font-mono tabular-nums">{r.price.toFixed(2)}</td>
                  <td className="px-2 py-2 text-right font-mono tabular-nums">{fmtMoney(r.value)}</td>
                  <td className="px-2 py-2 text-right font-mono tabular-nums">
                    {((r.value / detail.portfolio.total) * 100).toFixed(1)}%
                  </td>
                  <td className={cn("px-2 py-2 text-right font-mono tabular-nums", tone(r.pnl))}>
                    {fmtMoney(r.pnl)}（{fmtPct(r.pnlPct)}）
                  </td>
                  <td className="px-4 py-2 text-right font-mono tabular-nums">{r.openedAt}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {pending.length > 0 && (
        <div>
          <p className="mb-2 text-sm font-medium text-foreground">待撮合挂单</p>
          <OrderTable orders={pending} />
        </div>
      )}
    </div>
  )
}

function Orders({ orders }: { orders: AgentOrder[] }) {
  if (orders.length === 0) {
    return <p className="rounded-lg border border-border p-4 text-xs text-muted-foreground">还没有订单</p>
  }
  return <OrderTable orders={orders} />
}

function OrderTable({ orders, compact }: { orders: AgentOrder[]; compact?: boolean }) {
  const [open, setOpen] = useState<number | null>(null)
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border text-muted-foreground">
            <th className="px-4 py-2 text-left font-normal">股票</th>
            <th className="px-2 py-2 text-left font-normal">方向</th>
            <th className="px-2 py-2 text-right font-normal">委托</th>
            <th className="px-2 py-2 text-left font-normal">状态</th>
            {!compact && <th className="px-2 py-2 text-left font-normal">撮合日</th>}
            <th className="px-2 py-2 text-right font-normal">成交</th>
            <th className="px-2 py-2 text-right font-normal">费用</th>
            <th className="px-4 py-2 text-right font-normal">实现盈亏</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((o) => (
            <Fragment key={o.id}>
              <tr
                className="cursor-pointer border-b border-border/60 hover:bg-muted/30"
                onClick={() => setOpen(open === o.id ? null : o.id)}
              >
                <td className="px-4 py-2">
                  {o.name} <span className="font-mono text-muted-foreground">{o.code}</span>
                </td>
                <td className={cn("px-2 py-2", o.side === "buy" ? "text-up" : "text-down")}>
                  {o.side === "buy" ? "买入" : "卖出"}
                </td>
                <td className="px-2 py-2 text-right font-mono tabular-nums">
                  {o.shares} @ {o.limitPrice != null ? o.limitPrice.toFixed(2) : "市价"}
                </td>
                <td className="px-2 py-2" title={o.note}>
                  {ORDER_STATUS[o.status]}
                  {o.status !== "filled" && o.note && <span className="ml-1 text-muted-foreground">· {o.note}</span>}
                </td>
                {!compact && <td className="px-2 py-2 font-mono tabular-nums">{o.tradeDate ?? "—"}</td>}
                <td className="px-2 py-2 text-right font-mono tabular-nums">
                  {o.fillPrice != null ? `${o.filledShares} @ ${o.fillPrice.toFixed(2)}` : "—"}
                </td>
                <td className="px-2 py-2 text-right font-mono tabular-nums">{o.fee ? o.fee.toFixed(2) : "—"}</td>
                <td className={cn("px-4 py-2 text-right font-mono tabular-nums", tone(o.realizedPnl))}>
                  {o.realizedPnl != null ? fmtMoney(o.realizedPnl) : "—"}
                </td>
              </tr>
              {open === o.id && (
                <tr className="border-b border-border/60 bg-muted/20">
                  <td colSpan={compact ? 7 : 8} className="px-4 py-2.5 text-xs leading-relaxed text-muted-foreground">
                    <span className="text-foreground">下单理由：</span>
                    {o.reason || "—"}
                    {o.memoryIds.length > 0 && (
                      <span className="ml-2">· 引用经验 {o.memoryIds.map((m) => `#${m}`).join(" ")}</span>
                    )}
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Memories({ detail }: { detail: AgentDetail }) {
  const [showRetired, setShowRetired] = useState(false)
  const rows = detail.memories.filter((m) => showRetired || m.active)
  const retired = detail.memories.filter((m) => !m.active).length
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">
          agent 在盘后复盘中写下经验，下单时引用；周度反思按引用订单的实际收益评估，淘汰被证伪的条目
        </p>
        {retired > 0 && (
          <label className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
            显示已淘汰（{retired}）
            <Switch checked={showRetired} onCheckedChange={(v: boolean) => setShowRetired(v)} />
          </label>
        )}
      </div>
      {rows.length === 0 ? (
        <div className="flex h-40 flex-col items-center justify-center rounded-lg border border-dashed border-border">
          <Brain className="size-7 text-muted-foreground/60" />
          <p className="mt-2 text-xs text-muted-foreground">经验会在盘后复盘中逐步积累</p>
        </div>
      ) : (
        <div className="space-y-2">
          {rows.map((m) => (
            <div
              key={m.id}
              className={cn("rounded-lg border border-border px-4 py-3", !m.active && "opacity-55")}
            >
              <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                <span className="font-mono">#{m.id}</span>
                <span className="rounded bg-muted px-1.5 py-0.5 text-foreground/80">{MEMORY_KIND[m.kind] ?? m.kind}</span>
                {m.tags.map((t) => (
                  <span key={t} className="rounded border border-border px-1.5 py-0.5">
                    {t}
                  </span>
                ))}
                <span>{m.created}</span>
                {m.outcome && (
                  <span className={cn("ml-auto font-mono", tone(m.outcome.avgRetPct))}>
                    引用 {m.outcome.orders} 笔 · 均 {fmtPct(m.outcome.avgRetPct)} · 胜 {m.outcome.wins}
                  </span>
                )}
              </div>
              <p className={cn("mt-1.5 text-sm leading-relaxed text-foreground", !m.active && "line-through")}>
                {m.content}
              </p>
              {!m.active && m.retireReason && (
                <p className="mt-1 text-xs text-muted-foreground">淘汰原因：{m.retireReason}</p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function PlaybookView({ accountId, current }: { accountId: number; current: string }) {
  const q = useQuery({ queryKey: ["agentPlaybooks", accountId], queryFn: () => fetchPlaybooks(accountId) })
  const versions = q.data ?? []
  const [version, setVersion] = useState<number | null>(null)
  const shown = versions.find((v) => v.version === version) ?? versions[0]
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_280px]">
      <div className="min-w-0 rounded-lg border border-border p-5">
        <AgentMarkdown>{shown?.content ?? current}</AgentMarkdown>
      </div>
      <div className="space-y-1.5">
        <p className="text-xs text-muted-foreground">版本历史（周度反思时改写）</p>
        {versions.map((v) => (
          <button
            key={v.version}
            onClick={() => setVersion(v.version)}
            className={cn(
              "w-full rounded-lg border px-3 py-2 text-left transition-colors",
              shown?.version === v.version ? "border-primary/50 bg-accent/40" : "border-border hover:bg-muted/40",
            )}
          >
            <p className="text-sm font-medium text-foreground">
              v{v.version}
              <span className="ml-2 text-[11px] font-normal text-muted-foreground">{v.createdAt?.slice(0, 10)}</span>
            </p>
            <p className="mt-0.5 line-clamp-3 text-xs text-muted-foreground">{v.rationale}</p>
          </button>
        ))}
      </div>
    </div>
  )
}

function CreateDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean
  onClose: () => void
  onCreated: (id: number) => void
}) {
  const [name, setName] = useState("")
  const [cash, setCash] = useState("1000000")
  const [busy, setBusy] = useState(false)

  async function submit() {
    const initialCash = Number(cash)
    if (!name.trim() || !(initialCash >= 10000)) {
      toast.error("请填写名称，初始资金不少于 1 万元")
      return
    }
    setBusy(true)
    try {
      const a = await createAgentAccount({ name: name.trim(), initialCash })
      toast.success("已创建：下一个交易日盘前开始运行，也可以立即手动分析")
      onCreated(a.id)
      onClose()
      setName("")
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "创建失败")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && !busy && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>新建 AI 账户</DialogTitle>
          <DialogDescription>
            账户全站可见。基准为沪深 300，初始交易手册为通用的中期波段框架，之后由 agent 每周自行迭代。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <label className="text-xs text-muted-foreground">名称</label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="如：DeepSeek 一号" maxLength={64} />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs text-muted-foreground">初始资金（元）</label>
            <Input value={cash} onChange={(e) => setCash(e.target.value.replace(/[^\d]/g, ""))} inputMode="numeric" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button onClick={submit} disabled={busy}>
            {busy ? "创建中…" : "创建"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
