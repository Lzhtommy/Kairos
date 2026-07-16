import { useMemo, useState } from "react"
import { Sparkle, ArrowCounterClockwise } from "@phosphor-icons/react"
import { useLiveQuotes } from "@/lib/use-live-quotes"
import { INDUSTRIES, STRATEGIES } from "@/lib/mock-data"
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
  const { stocks } = useLiveQuotes()
  const [mode, setMode] = useState<"classic" | "strategy">("classic")
  const [industry, setIndustry] = useState<string>("all")
  const [peRange, setPeRange] = useState([0, 60])
  const [minRoe, setMinRoe] = useState([0])
  const [activeStrategy, setActiveStrategy] = useState<string | null>(STRATEGIES[0].id)

  const filtered = useMemo(() => {
    if (mode === "strategy") {
      if (!activeStrategy) return stocks
      // 演示用：策略结果按股票代码哈希稳定抽样
      return stocks.filter((s) => s.code.charCodeAt(4) % 2 === (activeStrategy === "st-2" ? 1 : 0))
    }
    return stocks.filter((s) => {
      if (industry !== "all" && s.industry !== industry) return false
      if (s.pe === null || s.pe < peRange[0] || s.pe > peRange[1]) return false
      if (s.roe < minRoe[0]) return false
      return true
    })
  }, [stocks, mode, industry, peRange, minRoe, activeStrategy])

  return (
    <div className="mx-auto max-w-[1600px] space-y-5 px-4 py-6 lg:px-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-foreground">选股</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            用传统指标筛选，或直接套用你与 AI 生成的策略
          </p>
        </div>
        <Tabs value={mode} onValueChange={(v) => setMode(v as typeof mode)}>
          <TabsList>
            <TabsTrigger value="classic">传统筛选</TabsTrigger>
            <TabsTrigger value="strategy">策略筛选</TabsTrigger>
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
                  <SelectContent>
                    <SelectItem value="all">全部行业</SelectItem>
                    {INDUSTRIES.map((ind) => (
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
                  onValueChange={(v) => setPeRange(v as number[])}
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
                  onValueChange={(v) => setMinRoe(v as number[])}
                  min={0}
                  max={30}
                  step={1}
                />
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              <p className="px-1 text-sm font-medium text-foreground">我的策略</p>
              {STRATEGIES.map((s) => (
                <button
                  key={s.id}
                  onClick={() => setActiveStrategy(s.id)}
                  className={cn(
                    "w-full rounded-lg border p-3 text-left transition-colors",
                    activeStrategy === s.id
                      ? "border-primary/40 bg-accent"
                      : "border-border hover:bg-muted/50",
                  )}
                >
                  <div className="flex items-center gap-1.5">
                    <Sparkle
                      weight={activeStrategy === s.id ? "fill" : "regular"}
                      className={cn(
                        "size-3.5",
                        activeStrategy === s.id ? "text-primary" : "text-muted-foreground",
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
          <p className="text-sm text-muted-foreground">
            共 <span className="font-mono font-medium text-foreground">{filtered.length}</span> 只符合条件
          </p>
          {filtered.length > 0 && (
            <div className="rounded-lg border border-border">
              <QuoteTable data={filtered} />
            </div>
          )}
          {filtered.length === 0 && (
            <div className="rounded-lg border border-dashed border-border py-14 text-center">
              <p className="text-sm text-muted-foreground">没有符合条件的股票，试着放宽筛选范围</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

