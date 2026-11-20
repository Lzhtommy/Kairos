import { useEffect, useMemo, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { ArrowSquareOut, MagnifyingGlass, X } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import {
  fetchNewsCategories,
  fetchNewsEvents,
  fetchNewsSources,
  type NewsEvent,
  type NewsSourceStatus,
  type NewsView,
} from "@/api/news"
import { cn } from "@/lib/utils"

const POLL_MS = 15_000
const PAGE = 50

// 快讯时间一律按北京时间显示，与 A 股交易时段对齐
const timeFmt = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
})
const dayKeyFmt = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai" }) // YYYY-MM-DD
const dayLabelFmt = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  month: "long",
  day: "numeric",
  weekday: "short",
})

function dayLabel(iso: string): string {
  const key = dayKeyFmt.format(new Date(iso))
  const today = dayKeyFmt.format(new Date())
  const yesterday = dayKeyFmt.format(new Date(Date.now() - 86400_000))
  const label = dayLabelFmt.format(new Date(iso))
  if (key === today) return `今天 · ${label}`
  if (key === yesterday) return `昨天 · ${label}`
  return label
}

function ago(iso: string | null): string {
  if (!iso) return "无数据"
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
  if (s < 60) return `${s} 秒前`
  if (s < 3600) return `${Math.round(s / 60)} 分钟前`
  return `${Math.round(s / 3600)} 小时前`
}

export function NewsPage() {
  const [view, setView] = useState<NewsView>("featured")
  const [category, setCategory] = useState<string | null>(null)
  const [draft, setDraft] = useState("")
  const [q, setQ] = useState("")
  const filterKey = `${view}|${category ?? ""}|${q}`
  const filters = { view, category: category ?? undefined, q }

  // 首页每 15 秒整页重拉：既拿到新事件，也拿到旧事件新增的报道源
  const head = useQuery({
    queryKey: ["news-head", view, category, q],
    queryFn: () => fetchNewsEvents({ ...filters, limit: PAGE }),
    refetchInterval: POLL_MS,
  })
  const sources = useQuery({
    queryKey: ["news-sources"],
    queryFn: fetchNewsSources,
    refetchInterval: 60_000,
  })
  const categories = useQuery({
    queryKey: ["news-categories"],
    queryFn: fetchNewsCategories,
    staleTime: Infinity,
  })

  // 「加载更多」拿到的更早的页，筛选条件变了就清空
  const [older, setOlder] = useState<{ key: string; events: NewsEvent[]; next: number | null }>({
    key: filterKey,
    events: [],
    next: null,
  })
  const [loadingMore, setLoadingMore] = useState(false)
  const olderFresh = older.key === filterKey ? older : { key: filterKey, events: [], next: null }

  const events = useMemo(() => {
    const byId = new Map<number, NewsEvent>()
    for (const e of olderFresh.events) byId.set(e.id, e)
    for (const e of head.data?.events ?? []) byId.set(e.id, e)
    return [...byId.values()].sort((a, b) => b.id - a.id)
  }, [head.data, olderFresh.events])

  const next = olderFresh.events.length ? olderFresh.next : (head.data?.next ?? null)

  async function loadMore() {
    if (next == null) return
    setLoadingMore(true)
    try {
      const page = await fetchNewsEvents({ ...filters, before: next, limit: PAGE })
      setOlder({ key: filterKey, events: [...olderFresh.events, ...page.events], next: page.next })
    } finally {
      setLoadingMore(false)
    }
  }

  // 新到的事件高亮几秒
  const known = useRef<Set<number> | null>(null)
  const [fresh, setFresh] = useState<Set<number>>(new Set())
  useEffect(() => {
    known.current = null
  }, [filterKey])
  useEffect(() => {
    const ids = head.data?.events.map((e) => e.id)
    if (!ids) return
    if (known.current == null) {
      known.current = new Set(ids)
      return
    }
    const added = ids.filter((id) => !known.current!.has(id))
    ids.forEach((id) => known.current!.add(id))
    if (!added.length) return
    setFresh(new Set(added))
    const t = setTimeout(() => setFresh(new Set()), 4000)
    return () => clearTimeout(t)
  }, [head.data])

  const groups = useMemo(() => {
    const out: { label: string; items: NewsEvent[] }[] = []
    for (const e of events) {
      const label = dayLabel(e.pub_at)
      if (out.at(-1)?.label !== label) out.push({ label, items: [] })
      out.at(-1)!.items.push(e)
    }
    return out
  }, [events])

  return (
    <div className="mx-auto max-w-4xl space-y-5 px-4 py-6 lg:px-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-foreground">快讯</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {"财联社、金十数据、华尔街见闻、新浪财经、同花顺 7×24 汇总，同一事件多家报道合并为一条；分类和要闻由 AI 按对市场的影响判断"}
          </p>
        </div>
        <SourceStatus sources={sources.data} />
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Tabs value={view} onValueChange={(v) => setView(v as NewsView)}>
          <TabsList>
            <TabsTrigger value="featured">要闻</TabsTrigger>
            <TabsTrigger value="all">全部</TabsTrigger>
          </TabsList>
        </Tabs>
        <form
          className="relative w-full max-w-xs"
          onSubmit={(e) => {
            e.preventDefault()
            setQ(draft.trim())
          }}
        >
          <MagnifyingGlass className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="搜索近 30 天快讯，回车确认"
            maxLength={50}
            className="pr-8 pl-8"
          />
          {q && (
            <button
              type="button"
              aria-label="清除搜索"
              onClick={() => {
                setDraft("")
                setQ("")
              }}
              className="absolute top-1/2 right-2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            >
              <X className="size-4" />
            </button>
          )}
        </form>
        <span className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground">
          <span className={cn("size-1.5 rounded-full", head.isError ? "bg-destructive" : "bg-primary animate-pulse")} />
          {head.isError ? "刷新失败，稍后自动重试" : "每 15 秒自动刷新"}
        </span>
      </div>

      {categories.data && (
        <div className="-mt-2 flex flex-wrap gap-1.5">
          {[null, ...categories.data].map((c) => (
            <button
              key={c ?? "all"}
              onClick={() => setCategory(c)}
              className={cn(
                "rounded-full border px-2.5 py-0.5 text-xs transition-colors",
                category === c
                  ? "border-primary bg-primary/10 text-foreground"
                  : "border-border text-muted-foreground hover:text-foreground",
              )}
            >
              {c ?? "全部分类"}
            </button>
          ))}
        </div>
      )}

      {head.isLoading ? (
        <div className="space-y-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : events.length === 0 ? (
        <div className="rounded-lg border border-dashed py-16 text-center text-sm text-muted-foreground">
          {q
            ? `近 30 天没有包含「${q}」的快讯`
            : category
              ? `暂无「${category}」类${view === "featured" ? "要闻" : "快讯"}`
              : "暂无快讯，采集启动后几十秒内会出现"}
        </div>
      ) : (
        <div className="space-y-6">
          {groups.map((g) => (
            <section key={g.label}>
              <h2 className="sticky top-0 z-10 -mx-1 bg-background/95 px-1 py-1.5 text-xs font-medium text-muted-foreground backdrop-blur">
                {g.label}
              </h2>
              <ol className="divide-y divide-border/60">
                {g.items.map((e) => (
                  <EventRow key={e.id} event={e} fresh={fresh.has(e.id)} />
                ))}
              </ol>
            </section>
          ))}
          {next != null && (
            <div className="flex justify-center">
              <Button variant="outline" onClick={loadMore} disabled={loadingMore}>
                {loadingMore ? "加载中…" : "加载更早的快讯"}
              </Button>
            </div>
          )}
        </div>
      )}

      <p className="text-xs text-muted-foreground">
        快讯内容版权归各来源媒体所有，点击来源查看原文。仅供参考，不构成投资建议。
      </p>
    </div>
  )
}

function SourceStatus({ sources }: { sources: NewsSourceStatus[] | undefined }) {
  if (!sources) return null
  return (
    <div className="flex items-center gap-3">
      {sources.map((s) => {
        // 15 分钟没有新条目：夜间个别源本来就少，只做提示，不算故障
        const stale = !s.last_seen || Date.now() - new Date(s.last_seen).getTime() > 15 * 60_000
        return (
          <Tooltip key={s.source}>
            <TooltipTrigger
              render={
                <span className="flex cursor-default items-center gap-1 text-xs text-muted-foreground">
                  <span
                    className={cn(
                      "size-1.5 rounded-full",
                      s.failing ? "bg-destructive" : stale ? "bg-muted-foreground/40" : "bg-emerald-500",
                    )}
                  />
                  {s.name}
                </span>
              }
            />
            <TooltipContent>
              {s.failing ? "接口请求失败，正在重试 · " : ""}最近更新 {ago(s.last_seen)} · 24 小时 {s.count_24h} 条
            </TooltipContent>
          </Tooltip>
        )
      })}
    </div>
  )
}

function EventRow({ event: e, fresh }: { event: NewsEvent; fresh: boolean }) {
  const [open, setOpen] = useState(false)
  const important = e.featured
  const long = e.content.length > 140
  return (
    <li
      className={cn(
        "flex gap-4 py-3 transition-colors duration-1000",
        fresh && "bg-primary/5",
      )}
    >
      <div className="w-11 shrink-0 pt-0.5 text-right">
        <time
          dateTime={e.pub_at}
          className={cn("font-mono text-xs tabular-nums", important ? "text-primary" : "text-muted-foreground")}
        >
          {timeFmt.format(new Date(e.pub_at))}
        </time>
      </div>
      <div className={cn("min-w-0 flex-1 border-l-2 pl-4", important ? "border-primary" : "border-transparent")}>
        {e.title && (
          <p className="text-sm font-medium text-foreground">{e.title}</p>
        )}
        {e.content && (
          <p
            onClick={() => long && setOpen(!open)}
            className={cn(
              "mt-0.5 text-sm leading-relaxed whitespace-pre-line",
              e.title ? "text-muted-foreground" : cn("text-foreground", important && "font-medium"),
              long && "cursor-pointer",
              long && !open && "line-clamp-3",
            )}
          >
            {e.content}
          </p>
        )}
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          {e.category && <span className="rounded bg-muted px-1.5 py-px text-foreground/80">{e.category}</span>}
          {e.stocks.slice(0, 4).map((st) => (
            <Link
              key={st.code}
              to={`/app/data/${st.code}`}
              className="font-medium text-foreground/90 underline-offset-2 hover:text-primary hover:underline"
            >
              {st.name}
            </Link>
          ))}
          {e.sectors.length > 0 && <span>{e.sectors.join(" · ")}</span>}
          <span className="ml-auto flex flex-wrap items-center gap-x-2.5 gap-y-1">
            {e.sources.map((s) =>
              s.url ? (
                <a
                  key={s.source}
                  href={s.url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-0.5 hover:text-foreground"
                >
                  {s.name}
                  <ArrowSquareOut className="size-3" />
                </a>
              ) : (
                <span key={s.source}>{s.name}</span>
              ),
            )}
            {e.sources.length >= 2 && <span className="tabular-nums">{e.sources.length} 家报道</span>}
          </span>
        </div>
      </div>
    </li>
  )
}
