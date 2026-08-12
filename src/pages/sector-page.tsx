import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { CaretDown, CaretUp } from "@phosphor-icons/react"
import { Badge } from "@/components/ui/badge"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { RRGChart } from "@/components/sector/rrg-chart"
import {
  fetchSectorNarrative,
  fetchSectorOverview,
  type NarrativeItem,
  type SectorRow,
} from "@/api/sector"
import { cn } from "@/lib/utils"

const SOURCE_LABEL: Record<string, string> = { sw: "申万一级", eastmoney: "东财板块", fake: "测试" }

function crowdTone(v: number | null | undefined): { dot: string; label: string } {
  if (v == null) return { dot: "bg-muted-foreground/30", label: "—" }
  if (v >= 0.8) return { dot: "bg-red-500", label: "过热" }
  if (v >= 0.6) return { dot: "bg-amber-500", label: "偏热" }
  return { dot: "bg-emerald-500", label: "正常" }
}

function TiltBadge({ suggestion }: { suggestion: string }) {
  if (suggestion === "高配")
    return <Badge className="bg-up-muted text-up border-transparent">高配</Badge>
  if (suggestion === "低配")
    return <Badge className="bg-down-muted text-down border-transparent">低配</Badge>
  return <Badge variant="secondary">标配</Badge>
}

function NarrativeCell({ item }: { item: NarrativeItem | null }) {
  if (!item || item.strength == null || item.strength === 0)
    return <span className="text-muted-foreground">—</span>
  const dir = item.direction ?? 0
  return (
    <span className="flex items-center gap-1.5" title={item.evidence?.join("\n")}>
      {dir > 0.1 ? (
        <CaretUp weight="bold" className="size-3 shrink-0 text-up" />
      ) : dir < -0.1 ? (
        <CaretDown weight="bold" className="size-3 shrink-0 text-down" />
      ) : null}
      <span className="truncate text-xs" title={item.summary}>
        {item.summary}
      </span>
    </span>
  )
}

type SortKey = "tilt" | "crowd" | "chg"

export function SectorPage() {
  const [sort, setSort] = useState<SortKey>("tilt")
  const overviewQ = useQuery({ queryKey: ["sector-overview"], queryFn: fetchSectorOverview })
  const narrativeQ = useQuery({
    queryKey: ["sector-narrative"],
    queryFn: () => fetchSectorNarrative(14),
  })

  const overview = overviewQ.data
  const sectors = useMemo(() => {
    const rows = overview?.sectors ?? []
    const key = {
      tilt: (s: SectorRow) => s.tilt?.rank ?? 999,
      crowd: (s: SectorRow) => -(s.crowd?.crowd ?? -1),
      chg: (s: SectorRow) => -(s.chg_pct ?? -999),
    }[sort]
    return rows.slice().sort((a, b) => key(a) - key(b))
  }, [overview, sort])

  const rrgSectors = useMemo(
    () =>
      (overview?.sectors ?? [])
        .filter((s) => s.rrg)
        .map((s) => ({ code: s.code, name: s.name, trail: s.rrg!.trail, rel4w: s.rrg!.rel4w })),
    [overview],
  )

  if (overviewQ.isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">加载中…</div>
  }

  if (!overview || !overview.sectors.length) {
    return (
      <div className="mx-auto max-w-[1600px] px-4 py-6 lg:px-6">
        <h1 className="text-lg font-semibold text-foreground">行业轮动</h1>
        <div className="mt-6 rounded-lg border border-dashed border-border py-14 text-center">
          <p className="text-sm text-muted-foreground">
            行业数据还在采集中——部署后的首个盘后任务会自动拉取板块历史，请稍后再来。
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-[1600px] space-y-6 px-4 py-6 lg:px-6">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold text-foreground">行业轮动</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            等权基准 ± 有限倾斜 + 过热预警——弱信号用低仓位变现，预警比预测靠谱。
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {overview.source && <Badge variant="outline">{SOURCE_LABEL[overview.source] ?? overview.source}</Badge>}
          {overview.asof && <span>截至 {overview.asof}</span>}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="rounded-lg border border-border p-4 lg:col-span-2">
          <div className="mb-1 flex items-baseline justify-between">
            <h2 className="text-sm font-medium">RRG 相对轮动图（周频）</h2>
            <span className="text-xs text-muted-foreground">
              轨迹为最近 6 周 · 通常按 改善→领先→转弱→落后 顺时针轮动
            </span>
          </div>
          {rrgSectors.length ? (
            <RRGChart sectors={rrgSectors} />
          ) : (
            <p className="py-10 text-center text-sm text-muted-foreground">
              历史深度不足（需约 32 周），随日更自动补齐。
            </p>
          )}
        </div>

        <div className="space-y-4">
          <div className="rounded-lg border border-border p-4">
            <h2 className="text-sm font-medium">今日市场叙事</h2>
            {overview.market_narrative ? (
              <>
                <p className="mt-2 text-sm">{overview.market_narrative.summary}</p>
                <p className="mt-1.5 text-xs text-muted-foreground">
                  方向 {(overview.market_narrative.direction ?? 0) >= 0 ? "+" : ""}
                  {(overview.market_narrative.direction ?? 0).toFixed(1)} · 强度{" "}
                  {(overview.market_narrative.strength ?? 0).toFixed(1)} ·{" "}
                  {overview.market_narrative.date}
                </p>
              </>
            ) : (
              <p className="mt-2 text-sm text-muted-foreground">今日暂无叙事记录。</p>
            )}
          </div>
          <div className="rounded-lg border border-border p-4 text-xs leading-relaxed text-muted-foreground">
            <p>
              <span className="font-medium text-foreground">怎么读：</span>
              倾斜建议来自月频三因子模型（上月反转 + 季节性 + 残差动量，样本外 IC
              +0.069 / t=2.73），月初更新{overview.tilt_month ? `（当前 ${overview.tilt_month} 月末信号）` : ""}；
              拥挤度是过热预警而非选股信号；叙事分只前向记录、不参与建议，
              读法与拥挤度配对——叙事升温且不拥挤是机会，叙事沸腾且高拥挤是出口。
            </p>
          </div>
        </div>
      </div>

      <div className="rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>行业</TableHead>
              <TableHead className="cursor-pointer" onClick={() => setSort("chg")}>
                涨跌{sort === "chg" && " ↓"}
              </TableHead>
              <TableHead className="cursor-pointer" onClick={() => setSort("crowd")}>
                拥挤度{sort === "crowd" && " ↓"}
              </TableHead>
              <TableHead className="cursor-pointer" onClick={() => setSort("tilt")}>
                倾斜建议{sort === "tilt" && " ↓"}
              </TableHead>
              <TableHead className="hidden md:table-cell">因子 z（反转/季节/残差动量）</TableHead>
              <TableHead className="hidden lg:table-cell">今日叙事</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sectors.map((s) => {
              const tone = crowdTone(s.crowd?.crowd)
              return (
                <TableRow key={s.code}>
                  <TableCell className="font-medium">{s.name}</TableCell>
                  <TableCell className="font-mono tabular-nums">
                    {s.chg_pct == null ? (
                      "—"
                    ) : (
                      <span className={cn(s.chg_pct >= 0 ? "text-up" : "text-down")}>
                        {s.chg_pct >= 0 ? "+" : ""}
                        {s.chg_pct.toFixed(2)}%
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    <span
                      className="flex items-center gap-2"
                      title={
                        s.crowd
                          ? `成交额占比 ${s.crowd.share ?? "—"} · 换手热度 ${s.crowd.heat ?? "—"} · 乖离率 ${s.crowd.bias ?? "—"}`
                          : undefined
                      }
                    >
                      <span className={cn("size-2 rounded-full", tone.dot)} />
                      <span className="font-mono text-xs tabular-nums">
                        {s.crowd?.crowd != null ? s.crowd.crowd.toFixed(2) : "—"}
                      </span>
                      <span className="text-xs text-muted-foreground">{tone.label}</span>
                    </span>
                  </TableCell>
                  <TableCell>
                    {s.tilt ? (
                      <span className="flex items-center gap-2">
                        <TiltBadge suggestion={s.tilt.suggestion} />
                        <span className="font-mono text-xs tabular-nums text-muted-foreground">
                          #{s.tilt.rank}
                        </span>
                      </span>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell className="hidden font-mono text-xs tabular-nums text-muted-foreground md:table-cell">
                    {s.tilt
                      ? [s.tilt.rev1_z, s.tilt.season_z, s.tilt.resmom_z]
                          .map((z) => (z == null ? "—" : (z >= 0 ? "+" : "") + z.toFixed(1)))
                          .join(" / ")
                      : "—"}
                  </TableCell>
                  <TableCell className="hidden max-w-72 lg:table-cell">
                    <NarrativeCell item={s.narrative} />
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>

      <div className="rounded-lg border border-border p-4">
        <h2 className="text-sm font-medium">叙事日志（最近 14 天，仅前向记录）</h2>
        {narrativeQ.data?.length ? (
          <div className="mt-3 space-y-4">
            {narrativeQ.data.map((day) => (
              <div key={day.date}>
                <p className="text-xs font-medium text-muted-foreground">
                  {day.date}
                  {day.market ? ` · ${day.market.summary}` : ""}
                </p>
                <ul className="mt-1.5 space-y-1">
                  {day.items.map((item) => (
                    <li key={`${day.date}-${item.code}`} className="flex items-baseline gap-2 text-sm">
                      <span className="w-20 shrink-0 font-medium">{item.name}</span>
                      <span
                        className={cn(
                          "shrink-0 font-mono text-xs tabular-nums",
                          (item.direction ?? 0) >= 0 ? "text-up" : "text-down",
                        )}
                      >
                        {(item.direction ?? 0) >= 0 ? "+" : ""}
                        {(item.direction ?? 0).toFixed(1)}
                      </span>
                      <span className="text-muted-foreground" title={item.evidence?.join("\n")}>
                        {item.summary}
                      </span>
                    </li>
                  ))}
                  {!day.items.length && (
                    <li className="text-sm text-muted-foreground">无行业级叙事。</li>
                  )}
                </ul>
              </div>
            ))}
          </div>
        ) : (
          <p className="mt-2 text-sm text-muted-foreground">
            暂无记录——叙事管道每日盘后运行（需生产环境配置 DeepSeek），从部署日起前向积累，
            攒满 12 个月样本后评估是否进模型。
          </p>
        )}
      </div>
    </div>
  )
}
