import { useEffect, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useSearchParams } from "react-router-dom"
import { toast } from "sonner"
import { Play, Sparkle, Trash } from "@phosphor-icons/react"
import { Button, buttonVariants } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { fetchStrategies, type Strategy } from "@/api/strategies"
import {
  submitBacktest,
  fetchBacktest,
  fetchBacktests,
  fetchBacktestTrades,
  deleteBacktest,
  type BacktestParams,
  type BacktestResult,
  type BacktestSummary,
  type BacktestTrade,
  type RebalanceRecord,
  type TradesPage,
} from "@/api/backtests"
import { BacktestChart } from "@/components/strategy/backtest-chart"
import { cn } from "@/lib/utils"

const EXIT_REASON: Record<BacktestTrade["reason"], string> = {
  hold: "到期",
  signal: "反向信号",
  stop_gain: "止盈",
  stop_loss: "止损",
}
const PERIOD_LABEL: Record<number, string> = { 120: "近半年", 250: "近1年", 500: "近2年", 750: "近3年" }
const EXIT_LABEL: Record<string, string> = { hold: "持有到期", signal: "反向信号", stop: "止盈止损" }
const REB_LABEL: Record<string, string> = { weekly: "每周", monthly: "每月", quarterly: "每季" }

const DEFAULT_PARAMS: BacktestParams = {
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
  maxConcurrent: 10,
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

function NumInput({
  label,
  value,
  min,
  max,
  suffix,
  onChange,
}: {
  label: string
  value: number
  min: number
  max: number
  suffix: string
  onChange: (n: number) => void
}) {
  const [text, setText] = useState(String(value))
  useEffect(() => setText(String(value)), [value])
  return (
    <label className="flex items-center gap-1.5" title={`${min}-${max}${suffix}`}>
      <span className="whitespace-nowrap text-xs text-muted-foreground">{label}</span>
      <input
        type="number"
        min={min}
        max={max}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => {
          const n = Math.round(Number(text))
          const v = Number.isFinite(n) && n >= min ? Math.min(max, n) : value
          setText(String(v))
          onChange(v)
        }}
        className="h-7 w-14 rounded-[min(var(--radius-md),10px)] border border-input bg-transparent px-2 text-center font-mono text-xs tabular-nums outline-none transition-colors focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30"
      />
      <span className="text-xs text-muted-foreground">{suffix}</span>
    </label>
  )
}

function summarizeBacktest(b: BacktestSummary): { params: string; result: string } {
  const p = b.params
  const m = b.metrics
  const bits = [PERIOD_LABEL[p.periodDays ?? 250] ?? `${p.periodDays}日`]
  if (m.mode === "event") {
    bits.push(
      `持有${p.holdDays}天`,
      p.exitRule === "stop"
        ? `止盈+${p.stopGain}%/止损-${p.stopLoss}%`
        : EXIT_LABEL[p.exitRule ?? "hold"],
    )
  } else {
    bits.push(REB_LABEL[p.rebalance ?? "monthly"], p.weighting === "cap" ? "市值加权" : "等权")
    if (p.maxPositions) bits.push(`前${p.maxPositions}`)
  }
  let result = ""
  if (b.status === "failed") result = "失败"
  else if (m.mode === "event") result = `平均 ${m.avgReturn}% · 胜率 ${m.winRate}%`
  else if (m.mode === "portfolio")
    result = `年化 ${m.annualizedReturn}%${m.excessReturn != null ? ` · 超额 ${m.excessReturn}%` : ""}`
  return { params: bits.join("·"), result }
}

function stockUrl(code: string) {
  const market = code.startsWith("6")
    ? "SH"
    : code.startsWith("4") || code.startsWith("8") || code.startsWith("9")
      ? "BJ"
      : "SZ"
  return `https://xueqiu.com/S/${market}${code}`
}

function TradeDetails({ backtestId }: { backtestId: number }) {
  const [data, setData] = useState<TradesPage | null>(null)
  const [page, setPage] = useState(1)
  const [sortKey, setSortKey] = useState("date_desc")

  useEffect(() => {
    let alive = true
    const [sort, order] =
      sortKey === "ret_desc" ? ["ret", "desc"] : sortKey === "ret_asc" ? ["ret", "asc"] : ["date", "desc"]
    fetchBacktestTrades(backtestId, page, sort as "date" | "ret", order as "asc" | "desc")
      .then((d) => alive && setData(d))
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [backtestId, page, sortKey])

  if (!data || data.total === 0) return null
  const totalPages = Math.max(1, Math.ceil(data.total / data.pageSize))

  return (
    <div className="rounded-lg border border-border">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
        <p className="text-xs font-medium text-foreground">
          {data.kind === "trades" ? "交易明细" : "调仓记录"}
          <span className="ml-1.5 font-mono text-muted-foreground">共 {data.total} {data.kind === "trades" ? "笔" : "期"}</span>
        </p>
        <div className="flex items-center gap-2">
          {data.kind === "trades" && (
            <ParamSelect
              label="排序"
              value={sortKey}
              onChange={(v) => {
                setSortKey(v)
                setPage(1)
              }}
              options={[["date_desc", "时间新→旧"], ["ret_desc", "收益高→低"], ["ret_asc", "收益低→高"]]}
            />
          )}
          {totalPages > 1 && (
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1}
                className="rounded border border-border px-1.5 py-0.5 hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
              >
                上一页
              </button>
              <span className="font-mono tabular-nums">{page}/{totalPages}</span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages}
                className="rounded border border-border px-1.5 py-0.5 hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
              >
                下一页
              </button>
            </div>
          )}
        </div>
      </div>
      {data.kind === "trades" ? (
        <div className="overflow-x-auto scrollbar-thin">
          <table className="w-full border-collapse text-left text-xs">
            <thead>
              <tr className="border-b border-border text-muted-foreground">
                {["入场日", "标的", "入场价", "出场日", "出场价", "收益", "持有", "退出"].map((h) => (
                  <th key={h} className="whitespace-nowrap px-3 py-1.5 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(data.items as BacktestTrade[]).map((t, i) => (
                <tr key={`${t.code}-${t.entryDate}-${i}`} className="border-b border-border/60 hover:bg-muted/50">
                  <td className="whitespace-nowrap px-3 py-1.5 font-mono tabular-nums">{t.entryDate}</td>
                  <td className="whitespace-nowrap px-3 py-1.5">
                    <a
                      href={stockUrl(t.code)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-foreground hover:text-primary"
                    >
                      {t.name}
                      <span className="ml-1 font-mono text-[10px] text-muted-foreground">{t.code}</span>
                    </a>
                  </td>
                  <td className="px-3 py-1.5 font-mono tabular-nums">{t.entryPx}</td>
                  <td className="whitespace-nowrap px-3 py-1.5 font-mono tabular-nums">{t.exitDate}</td>
                  <td className="px-3 py-1.5 font-mono tabular-nums">{t.exitPx}</td>
                  <td className={cn("px-3 py-1.5 font-mono tabular-nums", t.ret >= 0 ? "text-up" : "text-down")}>
                    {t.ret > 0 ? "+" : ""}{t.ret}%
                  </td>
                  <td className="px-3 py-1.5 font-mono tabular-nums">{t.days}天</td>
                  <td className="whitespace-nowrap px-3 py-1.5 text-muted-foreground">{EXIT_REASON[t.reason] ?? t.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="divide-y divide-border/60">
          {(data.items as RebalanceRecord[]).map((r) => (
            <div key={r.date} className="px-3 py-2 text-xs">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="font-mono tabular-nums text-foreground">{r.date}</span>
                <span className="text-muted-foreground">持仓 {r.holdings}</span>
                {r.addedCount > 0 && <span className="text-up">新进 {r.addedCount}</span>}
                {r.removedCount > 0 && <span className="text-down">调出 {r.removedCount}</span>}
                <span className={cn("ml-auto font-mono tabular-nums", r.periodReturn >= 0 ? "text-up" : "text-down")}>
                  期收益 {r.periodReturn > 0 ? "+" : ""}{r.periodReturn}%
                </span>
              </div>
              {(r.added.length > 0 || r.removed.length > 0) && (
                <p className="mt-1 text-muted-foreground">
                  {r.added.length > 0 && (
                    <>进：{r.added.slice(0, 6).map((s) => s.name).join("、")}{r.addedCount > 6 ? ` 等${r.addedCount}只` : ""}</>
                  )}
                  {r.added.length > 0 && r.removed.length > 0 && "　"}
                  {r.removed.length > 0 && (
                    <>出：{r.removed.slice(0, 6).map((s) => s.name).join("、")}{r.removedCount > 6 ? ` 等${r.removedCount}只` : ""}</>
                  )}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function BacktestHistory({
  items,
  currentId,
  onLoad,
  onDelete,
}: {
  items: BacktestSummary[]
  currentId: number | null
  onLoad: (b: BacktestSummary) => void
  onDelete: (b: BacktestSummary) => void
}) {
  if (items.length === 0) return null
  return (
    <div className="rounded-lg border border-border">
      <p className="border-b border-border px-3 py-2 text-xs font-medium text-foreground">
        历史回测 <span className="ml-1 font-mono text-muted-foreground">{items.length}</span>
      </p>
      <div className="max-h-56 divide-y divide-border/60 overflow-y-auto scrollbar-thin">
        {items.map((b) => {
          const s = summarizeBacktest(b)
          return (
            <div
              key={b.id}
              className={cn(
                "flex items-center gap-2 px-3 py-1.5 text-xs",
                currentId === b.id && "bg-accent/60",
              )}
            >
              <button
                onClick={() => onLoad(b)}
                disabled={b.status !== "done"}
                className="flex flex-1 flex-wrap items-center gap-x-3 gap-y-0.5 text-left disabled:opacity-50"
              >
                <span className="font-mono tabular-nums text-muted-foreground">{b.createdAt}</span>
                <span className="text-foreground">{s.params}</span>
                <span
                  className={cn(
                    "font-mono tabular-nums",
                    b.status === "failed" ? "text-down" : "text-muted-foreground",
                  )}
                >
                  {s.result}
                </span>
              </button>
              <button
                onClick={() => onDelete(b)}
                aria-label="删除该回测"
                className="flex size-5 shrink-0 items-center justify-center rounded text-muted-foreground hover:text-destructive"
              >
                <Trash className="size-3" />
              </button>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function BacktestPage() {
  const qc = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const strategiesQ = useQuery({ queryKey: ["strategies"], queryFn: fetchStrategies })
  const strategies = strategiesQ.data ?? []
  const paramId = searchParams.get("s")
  const active: Strategy | null =
    strategies.find((s) => s.id === paramId) ?? strategies[0] ?? null

  const [btParams, setBtParams] = useState<BacktestParams>(DEFAULT_PARAMS)
  const [backtest, setBacktest] = useState<BacktestResult | null>(null)
  const [btRunning, setBtRunning] = useState(false)
  const [chartView, setChartView] = useState<"equity" | "path">("equity")

  // 切换策略时清掉当前展示；自动加载效应会补上该策略最近一次的结果
  useEffect(() => {
    setBacktest(null)
  }, [active?.id])

  const historyQ = useQuery({
    queryKey: ["backtests", active?.id],
    queryFn: () => fetchBacktests(active!.id),
    enabled: !!active,
  })
  useEffect(() => {
    const latest = historyQ.data?.find((b) => b.status === "done")
    if (!latest || backtest || btRunning) return
    let alive = true
    fetchBacktest(latest.id)
      .then((res) => alive && setBacktest((cur) => cur ?? res))
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [historyQ.data, backtest, btRunning])

  const isEventStrategy =
    Array.isArray((active?.dsl as { technical?: unknown[] } | undefined)?.technical) &&
    ((active!.dsl as { technical: unknown[] }).technical.length > 0)

  function setParam<K extends keyof BacktestParams>(key: K, value: BacktestParams[K]) {
    setBtParams((p) => ({ ...p, [key]: value }))
  }

  async function runBacktest() {
    if (!active) return
    setBtRunning(true)
    setBacktest(null)
    try {
      const { id: bid } = await submitBacktest(active.id, btParams)
      for (let i = 0; i < 120; i++) {
        const res = await fetchBacktest(bid)
        if (res.status === "done" || res.status === "failed") {
          setBacktest(res)
          qc.invalidateQueries({ queryKey: ["backtests"] })
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

  async function loadHistoryBacktest(b: BacktestSummary) {
    try {
      setBacktest(await fetchBacktest(b.id))
    } catch {
      toast.error("载入回测失败")
    }
  }

  async function removeBacktest(b: BacktestSummary) {
    try {
      await deleteBacktest(b.id)
      qc.invalidateQueries({ queryKey: ["backtests"] })
      if (backtest?.id === b.id) setBacktest(null)
      toast.success("已删除该回测记录")
    } catch {
      toast.error("删除失败")
    }
  }

  const metrics = backtest?.status === "done" ? backtest.metrics : null

  return (
    <div className="mx-auto max-w-[1600px] space-y-5 px-4 py-6 lg:px-6">
      <div>
        <h1 className="text-lg font-semibold text-foreground">回测</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          选择策略、调整参数，检验它在历史行情中的表现
        </p>
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[260px_1fr]">
        {/* 策略选择 */}
        <aside className="space-y-2 lg:sticky lg:top-6 lg:self-start">
          <p className="px-1 text-sm font-medium text-foreground">我的策略</p>
          {strategies.length === 0 && !strategiesQ.isLoading && (
            <div className="rounded-lg border border-dashed border-border p-4 text-xs text-muted-foreground">
              还没有策略，先去
              <Link to="/app/strategy" className="mx-0.5 text-primary hover:underline">
                策略工坊
              </Link>
              创建一个
            </div>
          )}
          {strategies.map((s) => (
            <button
              key={s.id}
              onClick={() => setSearchParams({ s: s.id })}
              className={cn(
                "w-full rounded-lg border p-3 text-left transition-colors",
                active?.id === s.id
                  ? "border-primary/40 bg-accent"
                  : "border-border hover:bg-muted/50",
              )}
            >
              <div className="flex items-center gap-1.5">
                <Sparkle
                  weight={active?.id === s.id ? "fill" : "regular"}
                  className={cn(
                    "size-3.5",
                    active?.id === s.id ? "text-primary" : "text-muted-foreground",
                  )}
                />
                <span className="truncate text-sm font-medium text-foreground">{s.name}</span>
              </div>
              <p className="mt-1 font-mono text-[11px] text-muted-foreground">
                {s.createdAt} · 命中 {s.hitCount}
              </p>
            </button>
          ))}
        </aside>

        {/* 回测工作区 */}
        <div className="min-w-0 space-y-3">
          {active ? (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-medium text-foreground">{active.name}</p>
                <div className="flex gap-1.5">
                  {backtest?.status === "done" && (
                    <Link
                      to={`/app/strategy?s=${active.id}&diagnose=${backtest.id}`}
                      className={cn(buttonVariants({ variant: "outline", size: "sm" }), "gap-1.5")}
                      title="让 AI 基于这次回测结果归因诊断，并给出改进后的策略"
                    >
                      <Sparkle className="size-3.5" />
                      AI 诊断
                    </Link>
                  )}
                  <Button size="sm" className="gap-1.5" onClick={runBacktest} disabled={btRunning}>
                    <Play weight="fill" className="size-3.5" />
                    {btRunning ? "回测中…" : "运行回测"}
                  </Button>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-border bg-muted/30 p-3">
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
                    <NumInput
                      label="持有"
                      value={btParams.holdDays ?? 10}
                      min={1}
                      max={60}
                      suffix="天"
                      onChange={(n) => setParam("holdDays", n)}
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
                    <NumInput
                      label="持仓上限"
                      value={btParams.maxConcurrent ?? 10}
                      min={1}
                      max={50}
                      suffix="只"
                      onChange={(n) => setParam("maxConcurrent", n)}
                    />
                    {btParams.exitRule === "stop" && (
                      <>
                        <NumInput
                          label="止盈"
                          value={btParams.stopGain ?? 15}
                          min={1}
                          max={100}
                          suffix="%"
                          onChange={(n) => setParam("stopGain", n)}
                        />
                        <NumInput
                          label="止损"
                          value={btParams.stopLoss ?? 8}
                          min={1}
                          max={50}
                          suffix="%"
                          onChange={(n) => setParam("stopLoss", n)}
                        />
                      </>
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
                    <NumInput
                      label="持仓数（0=不限）"
                      value={btParams.maxPositions ?? 0}
                      min={0}
                      max={200}
                      suffix="只"
                      onChange={(n) => setParam("maxPositions", n)}
                    />
                  </>
                )}
              </div>

              {btRunning && (
                <div className="rounded-lg border border-dashed border-border py-14 text-center text-sm text-muted-foreground">
                  正在回测…
                </div>
              )}
              {!metrics && !btRunning && (
                <div className="rounded-lg border border-dashed border-border py-14 text-center text-sm text-muted-foreground">
                  调好参数后点击「运行回测」查看历史表现
                </div>
              )}
              {metrics && (
                <>
                  <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-3 lg:grid-cols-6">
                    {(metrics.mode === "event"
                      ? [
                          { label: "账户总收益", value: `${(metrics.totalReturn ?? 0) > 0 ? "+" : ""}${metrics.totalReturn}%`, tone: (metrics.totalReturn ?? 0) >= 0 ? ("up" as const) : ("down" as const) },
                          { label: "年化收益", value: `${(metrics.annualizedReturn ?? 0) > 0 ? "+" : ""}${metrics.annualizedReturn ?? "—"}%`, tone: (metrics.annualizedReturn ?? 0) >= 0 ? ("up" as const) : ("down" as const) },
                          { label: "最大回撤", value: `${metrics.maxDrawdown ?? "—"}%`, tone: "down" as const },
                          { label: `${metrics.benchmarkName ?? "基准"}同期`, value: metrics.benchmarkReturn == null ? "—" : `${metrics.benchmarkReturn > 0 ? "+" : ""}${metrics.benchmarkReturn}%`, tone: "flat" as const },
                          { label: "成交信号", value: `${metrics.eventCount}`, tone: "flat" as const },
                          { label: "胜率", value: `${metrics.winRate}%`, tone: "flat" as const },
                          { label: "平均单次收益", value: `${(metrics.avgReturn ?? 0) > 0 ? "+" : ""}${metrics.avgReturn}%`, tone: (metrics.avgReturn ?? 0) >= 0 ? ("up" as const) : ("down" as const) },
                          { label: "平均持有", value: `${metrics.avgHoldDays} 天`, tone: "flat" as const },
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
                  {backtest && backtest.curve.length > 1 && (
                    <div className="rounded-lg border border-border p-3">
                      {metrics.mode === "event" && backtest.avgPath.length > 1 && (
                        <div className="mb-2 flex items-center gap-1">
                          {(
                            [
                              ["equity", "资金曲线"],
                              ["path", "信号平均路径"],
                            ] as const
                          ).map(([key, label]) => (
                            <button
                              key={key}
                              onClick={() => setChartView(key)}
                              className={cn(
                                "rounded-md px-2 py-1 text-xs transition-colors",
                                chartView === key
                                  ? "bg-accent text-accent-foreground"
                                  : "text-muted-foreground hover:text-foreground",
                              )}
                            >
                              {label}
                            </button>
                          ))}
                          <span className="ml-2 text-[11px] text-muted-foreground">
                            {chartView === "equity"
                              ? "逐日等权持有全部在场信号的模拟资金曲线"
                              : "横轴为入场后交易日数（D0=入场日），曲线为全部信号的平均累计收益"}
                          </span>
                        </div>
                      )}
                      {metrics.mode === "event" && chartView === "path" ? (
                        <BacktestChart curve={backtest.avgPath} />
                      ) : (
                        <BacktestChart
                          curve={backtest.curve}
                          benchmark={backtest.benchmark}
                          benchmarkName={metrics.benchmarkName ?? "基准"}
                        />
                      )}
                    </div>
                  )}
                  {backtest?.status === "done" && (
                    <TradeDetails key={backtest.id} backtestId={backtest.id} />
                  )}
                  {metrics.mode === "event" &&
                    ((metrics.skippedByLimit ?? 0) > 0 || (metrics.skippedByCapacity ?? 0) > 0) && (
                      <p className="text-xs text-muted-foreground">
                        可交易性约束：
                        {(metrics.skippedByLimit ?? 0) > 0 &&
                          `${metrics.skippedByLimit} 个信号因连续一字涨停买不进而放弃`}
                        {(metrics.skippedByLimit ?? 0) > 0 && (metrics.skippedByCapacity ?? 0) > 0 && "；"}
                        {(metrics.skippedByCapacity ?? 0) > 0 &&
                          `${metrics.skippedByCapacity} 个信号因持仓已满（${metrics.maxConcurrent} 只上限）未成交`}
                        。
                      </p>
                    )}
                  <p className="text-xs text-muted-foreground">
                    {metrics.mode === "event"
                      ? `事件驱动回测（账户口径）：信号次日入场、按所选规则退出，每笔占 1/${metrics.maxConcurrent ?? 10} 仓位，同日信号多于空位时按代码序取前 N。止盈止损按盘中高低价触发、按触发价成交（跳空按开盘价，同日双触发保守计为止损）。一字涨停顺延入场（3 日买不进放弃）、一字跌停顺延出场。涨跌幅/最高价/最低价/开盘涨跌幅条件按信号日 K 线逐日检查，其余标量条件（PE/市值等）按今日快照预筛股票池。资金曲线为账户净值（空仓部分现金持平，虚线为基准）。`
                      : `组合回测：每个调仓期按当期时点数据重新选股（虚线为基准指数），调仓成本按实际换手比例计。时点因子来自每日收盘快照${
                          metrics.rebalances
                            ? `，本次 ${metrics.rebalances} 期中 ${metrics.pitPeriods ?? 0} 期有真实快照`
                            : ""
                        }；快照未覆盖的日期用价格重构近似。`}
                    股票池为当前上市股票，存在幸存者偏差——回测期内退市的股票不在池中，小市值/困境类策略收益会被高估。历史表现不代表未来收益。
                  </p>
                </>
              )}
              <BacktestHistory
                items={historyQ.data ?? []}
                currentId={backtest?.id ?? null}
                onLoad={loadHistoryBacktest}
                onDelete={removeBacktest}
              />
            </>
          ) : (
            !strategiesQ.isLoading && (
              <div className="rounded-lg border border-dashed border-border py-16 text-center">
                <p className="text-sm text-muted-foreground">
                  创建策略后即可在这里回测，
                  <Link
                    to="/app/strategy"
                    className={cn(buttonVariants({ variant: "link" }), "px-1")}
                  >
                    去策略工坊
                  </Link>
                </p>
              </div>
            )
          )}
        </div>
      </div>
    </div>
  )
}
