import { useState } from "react"
import { Link, useNavigate, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { ArrowLeft, DownloadSimple, Info, Trash, Warning } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  type AnnualCitation,
  type AnnualRisk,
  DIM_ORDER,
  deleteResearch,
  downloadResearchMarkdown,
  fetchResearch,
  fetchResearchHistory,
  type EventItem,
  type Fact,
  type NewsItem,
  type ResearchDetail,
  type ScoreItem,
  type Section,
  type SubScore,
} from "@/api/research"
import {
  DimensionBars,
  GradeBadge,
  Markdown,
  ScoreTrend,
} from "@/components/research/research-ui"
import { fmtDate, fmtDateTime, isStale } from "@/lib/research-format"
import { useAuth } from "@/lib/auth"
import { cn } from "@/lib/utils"

const DISCLAIMER = "本报告由程序基于公开数据自动生成，仅供研究参考，不构成投资建议。"

const SOURCE_FALLBACK: Record<string, string> = { factor_history: "本地行情快照" }

const NEGATIVE_TAGS = new Set(["reduce", "inquiry", "investigation", "forecast_down", "lawsuit", "audit_nonstandard"])

function fmtFact(f: Fact | undefined): string {
  if (!f) return "—"
  const v = f.value
  switch (f.unit) {
    case "%":
      return `${v.toFixed(2)}%`
    case "亿元":
      return `${v.toFixed(2)} 亿元`
    case "倍":
      return `${v.toFixed(2)} 倍`
    case "pp":
      return `${v.toFixed(2)} pp`
    default:
      return v.toFixed(2)
  }
}

export function ResearchDetailPage() {
  const { id } = useParams()
  const reportId = Number(id)
  const q = useQuery({ queryKey: ["research", reportId], queryFn: () => fetchResearch(reportId) })

  if (q.isLoading) return <div className="p-6 text-sm text-muted-foreground">加载中…</div>
  if (q.error || !q.data)
    return (
      <div className="p-6 text-sm text-muted-foreground">
        研报不存在或已删除 ·{" "}
        <Link to="/app/research" className="text-primary hover:underline">
          返回研报库
        </Link>
      </div>
    )
  return <ReportView report={q.data} />
}

function ReportView({ report }: { report: ResearchDetail }) {
  const { user } = useAuth()
  const historyQ = useQuery({
    queryKey: ["research-history", report.code],
    queryFn: () => fetchResearchHistory(report.code),
  })
  const sourceLabel = (key: string | null | undefined) =>
    report.sources.find((s) => s.key === key)?.label ?? SOURCE_FALLBACK[key ?? ""] ?? key ?? "—"

  const summary = report.sections.find((s) => s.key === "summary")
  const body = report.sections.filter((s) => s.key !== "summary")
  const toc = [...body.map((s) => ({ key: s.key, title: s.title })), { key: "appendix", title: "附录：评分明细" }]

  return (
    <div className="mx-auto max-w-6xl px-4 py-6 lg:px-6">
      <Link
        to="/app/research"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" />
        研报库
      </Link>

      {/* 头部 */}
      <div className="mt-3 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-foreground">
            {report.name}
            <span className="ml-2 font-mono text-base font-normal text-muted-foreground">{report.code}</span>
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {report.industry} · 报告期 {report.data_as_of ?? "—"}
            {isStale(report.data_as_of) && <span className="ml-1">（可能过期）</span>} · 生成于{" "}
            {fmtDateTime(report.finished_at)} · 规则 {report.scoring_version}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <VersionSelect report={report} history={historyQ.data ?? []} />
          <Button
            variant="outline"
            onClick={() =>
              downloadResearchMarkdown(report.id, `${report.name}-${report.code}-${fmtDate(report.finished_at)}.md`).catch(
                (e) => toast.error(e instanceof Error ? e.message : "下载失败"),
              )
            }
          >
            <DownloadSimple className="size-4" />
            Markdown
          </Button>
          {user?.is_admin && <DeleteVersion report={report} />}
        </div>
      </div>

      {report.updating && (
        <div className="mt-4 rounded-lg border border-primary/30 bg-primary/5 px-4 py-2.5 text-sm text-primary">
          该股票研报正在更新中，当前展示的是上一版本。
        </div>
      )}

      <div className="mt-4 flex items-start gap-2 rounded-lg bg-muted/60 px-4 py-2.5 text-xs text-muted-foreground">
        <Info className="mt-0.5 size-3.5 shrink-0" />
        {DISCLAIMER}
      </div>

      {/* 评分卡 */}
      <section className="mt-4 grid gap-6 rounded-lg border border-border p-5 md:grid-cols-[14rem_1fr]">
        <div className="space-y-4">
          <div className="flex items-center gap-4">
            <GradeBadge grade={report.grade} size="lg" />
            <div>
              <div className="text-xs text-muted-foreground">基本面评级</div>
              <div className="font-mono text-2xl font-semibold tabular-nums text-foreground">
                {report.total_score?.toFixed(1) ?? "—"}
                <span className="text-sm font-normal text-muted-foreground"> / 100</span>
              </div>
              <div className="text-xs text-muted-foreground">
                数据覆盖率 {Math.round((report.coverage ?? 0) * 100)}%
              </div>
            </div>
          </div>
          <ScoreTrend points={historyQ.data ?? []} currentId={report.id} />
        </div>
        <DimensionBars dims={report.sub_scores} />
      </section>

      {/* 排雷 */}
      {report.red_flags.length > 0 && (
        <section className="mt-4 rounded-lg border border-red-500/40 bg-red-500/5 p-4">
          <div className="flex items-center gap-2 text-sm font-medium text-red-600 dark:text-red-400">
            <Warning weight="fill" className="size-4" />
            排雷提示（评级封顶 C）
          </div>
          <ul className="mt-2 space-y-1 text-sm text-foreground/90">
            {report.red_flags.map((f) => (
              <li key={f.key}>
                <span className="font-medium">{f.label}</span>：{f.detail}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* 核心观察 */}
      {summary && (summary.observations?.length ?? 0) > 0 && (
        <section className="mt-4 rounded-lg border border-border p-5">
          <h2 className="text-sm font-medium text-foreground">核心观察</h2>
          <ol className="mt-3 space-y-2 text-sm leading-6 text-foreground/90">
            {summary.observations!.map((o, i) => (
              <li key={i} className="flex gap-3">
                <span className="font-mono text-indigo-500">{i + 1}</span>
                <span>{o}</span>
              </li>
            ))}
          </ol>
          <SectionWarning section={summary} />
        </section>
      )}

      {/* 正文 */}
      <div className="mt-8 grid gap-8 lg:grid-cols-[11rem_1fr]">
        <nav className="hidden lg:block">
          <div className="sticky top-4 space-y-1 text-sm">
            {toc.map((t, i) => (
              <a
                key={t.key}
                href={`#sec-${t.key}`}
                className="block rounded px-2 py-1 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <span className="mr-1.5 font-mono text-xs">{i + 2}</span>
                {t.title}
              </a>
            ))}
          </div>
        </nav>

        <div className="min-w-0 space-y-10">
          {body.map((s, i) => (
            <section key={s.key} id={`sec-${s.key}`} className="scroll-mt-4">
              <h2 className="mb-4 text-base font-semibold text-foreground">
                <span className="mr-2 font-mono text-sm text-muted-foreground">{i + 2}</span>
                {s.title}
              </h2>
              <div className="space-y-4">
                {s.key === "overview" && <Overview section={s} facts={report.facts} />}
                {s.metric_keys.map((m) =>
                  report.sub_scores[m] ? (
                    <MetricTable key={m} dim={report.sub_scores[m]} sourceLabel={sourceLabel} />
                  ) : null,
                )}
                {s.key === "events" && <EventList items={(s.items ?? []) as EventItem[]} />}
                {s.key === "news" && <NewsBlock section={s} />}
                {s.md ? (
                  <Markdown>{s.md}</Markdown>
                ) : (
                  <p className="text-sm text-muted-foreground italic">AI 解读不可用</p>
                )}
                <SectionWarning section={s} />
                {s.citations && s.citations.length > 0 && <Citations items={s.citations} />}
                {s.company_risks && s.company_risks.length > 0 && <CompanyRisks items={s.company_risks} />}
              </div>
            </section>
          ))}

          <section id="sec-appendix" className="scroll-mt-4">
            <h2 className="mb-4 text-base font-semibold text-foreground">
              <span className="mr-2 font-mono text-sm text-muted-foreground">{body.length + 2}</span>
              附录：评分明细与数据来源
            </h2>
            <Appendix report={report} sourceLabel={sourceLabel} />
          </section>

          <p className="border-t border-border pt-4 text-xs text-muted-foreground">{DISCLAIMER}</p>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- 头部控件

function VersionSelect({
  report,
  history,
}: {
  report: ResearchDetail
  history: { id: number; finished_at: string | null; total_score: number | null; grade: string | null }[]
}) {
  const navigate = useNavigate()
  if (history.length < 2) return null
  return (
    <Select value={String(report.id)} onValueChange={(v) => v && navigate(`/app/research/${v}`)}>
      <SelectTrigger className="w-52">
        <SelectValue>{`版本 ${fmtDate(report.finished_at)}`}</SelectValue>
      </SelectTrigger>
      <SelectContent alignItemWithTrigger={false}>
        {history
          .slice()
          .reverse()
          .map((h) => (
            <SelectItem key={h.id} value={String(h.id)}>
              {fmtDateTime(h.finished_at)} · {h.grade ?? "—"} · {h.total_score?.toFixed(1) ?? "—"}
            </SelectItem>
          ))}
      </SelectContent>
    </Select>
  )
}

function DeleteVersion({ report }: { report: ResearchDetail }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [armed, setArmed] = useState(false)
  const del = useMutation({
    mutationFn: () => deleteResearch(report.id),
    onSuccess: () => {
      toast.success("已删除该版本")
      qc.invalidateQueries({ queryKey: ["research-list"] })
      qc.invalidateQueries({ queryKey: ["research-history", report.code] })
      navigate("/app/research")
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "删除失败"),
  })
  return (
    <Button
      variant={armed ? "destructive" : "outline"}
      onClick={() => (armed ? del.mutate() : setArmed(true))}
      onBlur={() => setArmed(false)}
      disabled={del.isPending}
    >
      <Trash className="size-4" />
      {armed ? "确认删除" : "删除"}
    </Button>
  )
}

// ---------------------------------------------------------------- 章节内容

function SectionWarning({ section }: { section: Section }) {
  if (!section.warning) return null
  return (
    <div className="flex items-start gap-2 rounded-md bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
      <Warning className="mt-0.5 size-3.5 shrink-0" />
      {section.warning}
    </div>
  )
}

function ratioOf(it: ScoreItem): number | null {
  if (it.points == null || it.max == null) return null
  return it.points / it.max
}

function MetricTable({ dim, sourceLabel }: { dim: SubScore; sourceLabel: (k?: string | null) => string }) {
  const isEvents = dim.items.length > 0 && dim.items[0].max == null
  return (
    <div className="rounded-lg border border-border">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <span className="text-sm font-medium text-foreground">{dim.label}</span>
        <span className="font-mono text-sm tabular-nums">
          {dim.score == null ? (
            <span className="text-muted-foreground">数据不足</span>
          ) : (
            <>
              {dim.score.toFixed(1)}
              <span className="text-muted-foreground"> / {dim.max}</span>
            </>
          )}
        </span>
      </div>
      {dim.items.length === 0 ? (
        <p className="px-4 py-3 text-xs text-muted-foreground">
          {isEvents || dim.label === "事件" ? "近 180 天无加减分事件，按基准分计" : "无数据"}
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>指标</TableHead>
              <TableHead className="text-right">数值</TableHead>
              <TableHead className="w-40">得分</TableHead>
              <TableHead className="hidden text-right sm:table-cell">零分点 → 满分点</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {dim.items.map((it) => {
              const r = ratioOf(it)
              return (
                <TableRow key={it.key}>
                  <TableCell className="text-sm">
                    {it.label}
                    {it.note && <div className="text-[11px] text-muted-foreground">{it.note}</div>}
                  </TableCell>
                  <TableCell
                    className="text-right font-mono text-sm tabular-nums"
                    title={it.source ? `来源：${sourceLabel(it.source)}${it.period ? ` · ${it.period}` : ""}` : undefined}
                  >
                    {it.display}
                  </TableCell>
                  <TableCell>
                    {isEvents ? (
                      <span
                        className={cn(
                          "font-mono text-xs tabular-nums",
                          (it.points ?? 0) < 0 ? "text-red-600 dark:text-red-400" : "text-indigo-600 dark:text-indigo-400",
                        )}
                      >
                        {(it.points ?? 0) > 0 ? "+" : ""}
                        {it.points}
                      </span>
                    ) : it.points == null ? (
                      <span className="text-xs text-muted-foreground">缺失，不计分</span>
                    ) : (
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                          <div className="h-full rounded-full bg-indigo-500" style={{ width: `${(r ?? 0) * 100}%` }} />
                        </div>
                        <span className="w-14 text-right font-mono text-xs tabular-nums">
                          {it.points.toFixed(1)}/{it.max}
                        </span>
                      </div>
                    )}
                  </TableCell>
                  <TableCell className="hidden text-right font-mono text-xs text-muted-foreground tabular-nums sm:table-cell">
                    {it.zero == null ? "—" : `${it.zero} → ${it.full}`}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}
    </div>
  )
}

function Overview({ section, facts }: { section: Section; facts: Record<string, Fact> }) {
  const p = section.profile ?? {}
  const rows = [
    ["公司名称", p["公司名称"]],
    ["所属行业（证监会）", p["所属行业"]],
    ["上市日期", p["上市日期"]],
    ["主营业务", p["主营业务"]],
  ].filter(([, v]) => v)
  const segs = section.segments ?? []
  const kpis = section.kpis ?? []
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <dl className="space-y-2 rounded-lg border border-border p-4 text-sm">
        {rows.map(([k, v]) => (
          <div key={k} className="grid grid-cols-[7rem_1fr] gap-2">
            <dt className="text-muted-foreground">{k}</dt>
            <dd className="text-foreground">{v}</dd>
          </div>
        ))}
      </dl>
      {segs.length > 0 && (
        <div className="rounded-lg border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>主营构成</TableHead>
                <TableHead className="text-right">收入占比</TableHead>
                <TableHead className="text-right">毛利率</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {segs.map((s) => (
                <TableRow key={s.name}>
                  <TableCell className="text-sm">{s.name}</TableCell>
                  <TableCell className="text-right font-mono text-sm tabular-nums">{fmtFact(facts[s.share_fact])}</TableCell>
                  <TableCell className="text-right font-mono text-sm tabular-nums">{fmtFact(facts[s.gm_fact])}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      {kpis.length > 0 && (
        <div className="rounded-lg border border-border md:col-span-2">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>经营数据（摘自定期报告）</TableHead>
                <TableHead className="text-right">数值</TableHead>
                <TableHead className="text-right">出处</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {kpis.map((k, i) => (
                <TableRow key={i}>
                  <TableCell className="text-sm" title={k.quote}>
                    {k.name}
                  </TableCell>
                  <TableCell className="text-right font-mono text-sm tabular-nums">
                    {k.value}
                    <span className="ml-1 font-sans text-xs text-muted-foreground">{k.unit}</span>
                  </TableCell>
                  <TableCell className="text-right text-xs whitespace-nowrap text-muted-foreground">
                    {docLabel(k)} 第 {k.page} 页
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  )
}

const docLabel = (item: { doc?: string }) => item.doc ?? "年报"

/** 本章解读引用的定期报告原文，默认折叠 */
function Citations({ items }: { items: AnnualCitation[] }) {
  return (
    <details className="group rounded-lg border border-border text-sm">
      <summary className="cursor-pointer select-none px-4 py-2.5 text-muted-foreground hover:text-foreground">
        定期报告原文 {items.length} 条
      </summary>
      <ul className="divide-y divide-border border-t border-border">
        {items.map((c, i) => (
          <li key={i} className="px-4 py-2.5">
            <div className="text-foreground">{c.claim}</div>
            <blockquote className="mt-1 border-l-2 border-border pl-3 text-xs text-muted-foreground">
              「{c.quote}」
              <span className="ml-1">
                — {docLabel(c)} 第 {c.page} 页
              </span>
            </blockquote>
          </li>
        ))}
      </ul>
    </details>
  )
}

function CompanyRisks({ items }: { items: AnnualRisk[] }) {
  return (
    <div className="rounded-lg border border-border">
      <div className="border-b border-border px-4 py-2.5 text-sm font-medium">公司自述风险（定期报告原文）</div>
      <ul className="divide-y divide-border text-sm">
        {items.map((r, i) => (
          <li key={i} className="px-4 py-2.5">
            <div className="font-medium text-foreground">{r.title}</div>
            <div className="mt-0.5 text-xs text-muted-foreground">
              {r.quote}
              <span className="ml-1">
                — {docLabel(r)} 第 {r.page} 页
              </span>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}

function EventList({ items }: { items: EventItem[] }) {
  if (items.length === 0) return <p className="text-sm text-muted-foreground">近 180 天无已识别的重大事项公告。</p>
  return (
    <ul className="divide-y divide-border rounded-lg border border-border text-sm">
      {items.map((e, i) => (
        <li key={i} className="flex items-start gap-3 px-4 py-2.5">
          <span className="w-20 shrink-0 font-mono text-xs text-muted-foreground tabular-nums">{e.date}</span>
          <span
            className={cn(
              "shrink-0 rounded px-1.5 py-0.5 text-[11px]",
              NEGATIVE_TAGS.has(e.tag)
                ? "bg-red-500/10 text-red-600 dark:text-red-400"
                : "bg-indigo-500/10 text-indigo-700 dark:text-indigo-300",
            )}
          >
            {e.label}
          </span>
          {e.url ? (
            <a href={e.url} target="_blank" rel="noreferrer" className="min-w-0 text-foreground/90 hover:underline">
              {e.title}
            </a>
          ) : (
            <span className="min-w-0 text-foreground/90">{e.title}</span>
          )}
        </li>
      ))}
    </ul>
  )
}

function NewsBlock({ section }: { section: Section }) {
  const news = (section.items ?? []) as NewsItem[]
  const reports = section.reports ?? []
  if (news.length === 0 && reports.length === 0) return null
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {news.length > 0 && (
        <div className="rounded-lg border border-border">
          <div className="border-b border-border px-4 py-2.5 text-sm font-medium">近期新闻</div>
          <ul className="divide-y divide-border text-sm">
            {news.slice(0, 8).map((n, i) => (
              <li key={i} className="px-4 py-2">
                <a href={n.url ?? undefined} target="_blank" rel="noreferrer" className="line-clamp-1 hover:underline">
                  {n.title}
                </a>
                <div className="text-[11px] text-muted-foreground">
                  {n.time.slice(0, 10)} · {n.source}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
      {reports.length > 0 && (
        <div className="rounded-lg border border-border">
          <div className="border-b border-border px-4 py-2.5 text-sm font-medium">近 90 天机构研报</div>
          <ul className="divide-y divide-border text-sm">
            {reports.map((r, i) => (
              <li key={i} className="px-4 py-2">
                <a href={r.url ?? undefined} target="_blank" rel="noreferrer" className="line-clamp-1 hover:underline">
                  {r.title}
                </a>
                <div className="text-[11px] text-muted-foreground">
                  {r.date} · {r.org} · {r.rating || "未评级"}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

const SOURCE_STATUS: Record<string, string> = { ok: "实时", cache: "缓存", stale: "过期缓存", failed: "失败" }

function Appendix({ report, sourceLabel }: { report: ResearchDetail; sourceLabel: (k?: string | null) => string }) {
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>维度</TableHead>
              <TableHead>指标</TableHead>
              <TableHead className="text-right">数值</TableHead>
              <TableHead className="text-right">得分</TableHead>
              <TableHead className="hidden text-right md:table-cell">零分点 → 满分点</TableHead>
              <TableHead className="hidden md:table-cell">来源</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {DIM_ORDER.flatMap((k) =>
              (report.sub_scores[k]?.items ?? []).map((it) => (
                <TableRow key={`${k}-${it.key}`}>
                  <TableCell className="text-xs text-muted-foreground">{report.sub_scores[k].label}</TableCell>
                  <TableCell className="text-sm">{it.label}</TableCell>
                  <TableCell className="text-right font-mono text-sm tabular-nums">{it.display}</TableCell>
                  <TableCell className="text-right font-mono text-xs tabular-nums">
                    {it.points == null ? "—" : it.max == null ? it.points : `${it.points} / ${it.max}`}
                  </TableCell>
                  <TableCell className="hidden text-right font-mono text-xs text-muted-foreground tabular-nums md:table-cell">
                    {it.zero == null ? "—" : `${it.zero} → ${it.full}`}
                  </TableCell>
                  <TableCell className="hidden text-xs text-muted-foreground md:table-cell">
                    {it.source ? `${sourceLabel(it.source)}${it.period ? ` · ${it.period}` : ""}` : "—"}
                  </TableCell>
                </TableRow>
              )),
            )}
          </TableBody>
        </Table>
      </div>
      <p className="text-xs leading-5 text-muted-foreground">
        计分规则：每个指标在「零分点 → 满分点」之间线性插值、两端截断；缺失指标不计分，维度按剩余权重折算；维度可用满分不足一半记为「数据不足」；
        整体覆盖率低于 70% 不给评级；命中排雷项评级封顶 C。评分完全由程序按规则计算，AI 只负责文字解读。
      </p>
      <div className="rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>数据源</TableHead>
              <TableHead>抓取时间</TableHead>
              <TableHead>状态</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {report.sources.map((s) => (
              <TableRow key={s.key}>
                <TableCell className="text-sm">
                  {s.url ? (
                    <a href={s.url} target="_blank" rel="noreferrer" className="hover:underline">
                      {s.label}
                    </a>
                  ) : (
                    s.label
                  )}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">{fmtDateTime(s.fetched_at)}</TableCell>
                <TableCell className={cn("text-xs", s.status === "failed" && "text-red-600 dark:text-red-400")}>
                  {SOURCE_STATUS[s.status] ?? s.status}
                  {s.error && <span className="ml-1 text-muted-foreground">（{s.error}）</span>}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {report.llm_model && <p className="text-xs text-muted-foreground">文字解读模型：{report.llm_model}</p>}
    </div>
  )
}
