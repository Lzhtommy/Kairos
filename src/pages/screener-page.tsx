import { useState } from "react"
import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { Sparkle, ArrowCounterClockwise, ArrowsClockwise, CircleNotch } from "@phosphor-icons/react"
import { INDUSTRIES } from "@/lib/mock-data"
import { fetchIndustries, runScreener } from "@/api/market"
import { fetchStrategies, fetchStrategyHits } from "@/api/strategies"
import { QuoteTable } from "@/components/market/quote-table"
import { Link } from "react-router-dom"
import { buttonVariants } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Slider } from "@/components/ui/slider"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { cn } from "@/lib/utils"

export function ScreenerPage() {
  const [mode, setMode] = useState<"classic" | "strategy">("strategy")
  const [industry, setIndustry] = useState<string>("all")
  const [peRange, setPeRange] = useState([0, 60])
  const [minRoe, setMinRoe] = useState([0])
  const [activeStrategy, setActiveStrategy] = useState<string | null>(null)

  const classicQ = useQuery({
    queryKey: ["screener", industry, peRange, minRoe],
    queryFn: () =>
      runScreener({
        industry,
        peMin: peRange[0],
        peMax: peRange[1],
        roeMin: minRoe[0],
        // 拉全量命中，翻页交给 QuoteTable 客户端分页（全市场也就 ~5500 只）
        pageSize: 10_000,
      }),
    enabled: mode === "classic",
    refetchInterval: 60_000,
    placeholderData: keepPreviousData, // 改筛选条件时保留旧列表，避免闪空
  })

  const industriesQ = useQuery({
    queryKey: ["industries"],
    queryFn: fetchIndustries,
    staleTime: 24 * 60 * 60 * 1000, // 行业分类一天刷一次，会话内不重复拉
    enabled: mode === "classic",
  })
  const industries = industriesQ.data?.length ? industriesQ.data : INDUSTRIES

  const strategiesQ = useQuery({
    queryKey: ["strategies"],
    queryFn: fetchStrategies,
    enabled: mode === "strategy",
  })

  const effectiveStrategy = activeStrategy ?? strategiesQ.data?.[0]?.id ?? null

  const hitsQ = useQuery({
    queryKey: ["strategyHits", effectiveStrategy],
    queryFn: () => fetchStrategyHits(effectiveStrategy!),
    enabled: mode === "strategy" && !!effectiveStrategy,
    placeholderData: keepPreviousData,
  })

  const filtered = mode === "classic" ? classicQ.data?.items ?? [] : hitsQ.data?.items ?? []
  const total =
    (mode === "classic" ? classicQ.data?.total : hitsQ.data?.total) ?? filtered.length
  const strategies = strategiesQ.data ?? []
  // 只在首次加载或筛选条件变化（展示的是旧数据占位）时亮加载态，60s 后台轮询不打扰
  const activeQ = mode === "classic" ? classicQ : hitsQ
  const loading = activeQ.isLoading || (activeQ.isFetching && activeQ.isPlaceholderData)

  return (
    <div className="mx-auto max-w-[1600px] space-y-5 px-4 py-6 lg:px-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-foreground">选股</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            直接套用你与 AI 生成的策略，或用传统指标筛选
          </p>
        </div>
        <Tabs value={mode} onValueChange={(v) => setMode(v as typeof mode)}>
          <TabsList>
            <TabsTrigger value="strategy">策略筛选</TabsTrigger>
            <TabsTrigger value="classic">传统筛选</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[260px_1fr]">
        <aside className="space-y-5 lg:sticky lg:top-6 lg:self-start">
          {mode === "classic" ? (
            <div className="space-y-5 rounded-lg border border-border p-4">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-foreground">筛选条件</span>
                <button
                  onClick={() => {
                    setIndustry("all")
                    setPeRange([0, 60])
                    setMinRoe([0])
                  }}
                  className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                >
                  <ArrowCounterClockwise className="size-3" />
                  重置
                </button>
              </div>

              <div className="space-y-2">
                <Label className="text-xs text-muted-foreground">行业</Label>
                <Select value={industry} onValueChange={(v) => setIndustry(v ?? "all")}>
                  <SelectTrigger className="w-full">
                    <SelectValue>
                      {(value: string) => (value === "all" ? "全部行业" : value)}
                    </SelectValue>
                  </SelectTrigger>
                  {/* 124 个行业选项时 alignItemWithTrigger 的对齐测量会卡死渲染进程，改普通下拉定位 */}
                  <SelectContent alignItemWithTrigger={false} className="max-h-72">
                    <SelectItem value="all">全部行业</SelectItem>
                    {industries.map((ind) => (
                      <SelectItem key={ind} value={ind}>
                        {ind}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label className="text-xs text-muted-foreground">市盈率 (PE)</Label>
                  <span className="font-mono text-xs tabular-nums text-foreground">
                    {peRange[0]} - {peRange[1]}
                  </span>
                </div>
                <Slider
                  value={peRange}
                  onValueChange={(v) => setPeRange(Array.isArray(v) ? v : [v])}
                  min={0}
                  max={60}
                  step={1}
                />
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label className="text-xs text-muted-foreground">最低 ROE</Label>
                  <span className="font-mono text-xs tabular-nums text-foreground">
                    {minRoe[0]}%
                  </span>
                </div>
                <Slider
                  value={minRoe}
                  // 单滑块时 base-ui 回传裸数字，不归一化的话 minRoe[0] 会变 undefined
                  onValueChange={(v) => setMinRoe(Array.isArray(v) ? v : [v])}
                  min={0}
                  max={30}
                  step={1}
                />
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              <p className="px-1 text-sm font-medium text-foreground">我的策略</p>
              {strategies.length === 0 && (
                <p className="px-1 text-xs text-muted-foreground">
                  还没有策略，去策略工坊用 AI 生成一个吧
                </p>
              )}
              {strategies.map((s) => (
                <button
                  key={s.id}
                  onClick={() => setActiveStrategy(s.id)}
                  className={cn(
                    "w-full rounded-lg border p-3 text-left transition-colors",
                    effectiveStrategy === s.id
                      ? "border-primary/40 bg-accent"
                      : "border-border hover:bg-muted/50",
                  )}
                >
                  <div className="flex items-center gap-1.5">
                    <Sparkle
                      weight={effectiveStrategy === s.id ? "fill" : "regular"}
                      className={cn(
                        "size-3.5",
                        effectiveStrategy === s.id ? "text-primary" : "text-muted-foreground",
                      )}
                    />
                    <span className="text-sm font-medium text-foreground">{s.name}</span>
                  </div>
                  <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                    {s.description}
                  </p>
                  <div className="mt-2 flex items-center justify-between">
                    <div className="flex gap-1">
                      {s.tags.map((t) => (
                        <span
                          key={t}
                          className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
                        >
                          {t}
                        </span>
                      ))}
                    </div>
                    <span className="font-mono text-[11px] text-muted-foreground">
                      命中 {s.hitCount}
                    </span>
                  </div>
                </button>
              ))}
              <Link
                to="/app/strategy"
                className={cn(buttonVariants({ variant: "outline" }), "w-full")}
              >
                <Sparkle className="size-4" />
                创建新策略
              </Link>
            </div>
          )}
        </aside>

        <div className="min-w-0 space-y-3">
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            共 <span className="font-mono font-medium text-foreground">{total}</span> 只符合条件
            {loading && <CircleNotch className="size-3.5 animate-spin" />}
            <button
              onClick={() => activeQ.refetch()}
              disabled={activeQ.isFetching}
              aria-label="刷新结果"
              title="重新计算筛选结果"
              className="flex size-6 items-center justify-center rounded text-muted-foreground transition-colors hover:text-foreground disabled:pointer-events-none"
            >
              <ArrowsClockwise className={cn("size-3.5", activeQ.isFetching && "animate-spin")} />
            </button>
          </p>
          {filtered.length > 0 && (
            <div
              className={cn(
                "rounded-lg border border-border transition-opacity",
                loading && "pointer-events-none opacity-50",
              )}
            >
              {/* key 让筛选条件 / 策略变化时分页回到第一页 */}
              <QuoteTable
                key={
                  mode === "classic"
                    ? `classic-${industry}-${peRange.join()}-${minRoe[0]}`
                    : `strategy-${effectiveStrategy}`
                }
                data={filtered}
              />
            </div>
          )}
          {filtered.length === 0 && loading && (
            <div className="flex items-center justify-center gap-2 rounded-lg border border-dashed border-border py-14">
              <CircleNotch className="size-4 animate-spin text-muted-foreground" />
              <p className="text-sm text-muted-foreground">筛选中…</p>
            </div>
          )}
          {filtered.length === 0 && !loading && (
            <div className="rounded-lg border border-dashed border-border py-14 text-center">
              <p className="text-sm text-muted-foreground">没有符合条件的股票，试着放宽筛选范围</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
