import { type ReactNode, useEffect, useMemo, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { MagnifyingGlass } from "@phosphor-icons/react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { LineChart } from "@/components/data/line-chart"
import { Markdown } from "@/components/research/research-ui"
import {
  fetchAnnouncements,
  fetchIndexCatalog,
  fetchIndexDetail,
  fetchKline,
  fetchMacroCatalog,
  fetchMacroSeries,
  fetchSecurity,
  fetchStockProfile,
  searchStocks,
  summarizeAnnouncement,
  type AnnouncementPage,
  type MacroCatalogItem,
  type StockProfile,
} from "@/api/data"
import { ApiError } from "@/api/client"
import { cn } from "@/lib/utils"

const MARKET_LABEL: Record<string, string> = { SH: "沪市", SZ: "深市", BJ: "北交所" }

function num(v: number | null | undefined, digits = 2) {
  return v == null ? "—" : v.toFixed(digits)
}

function yi(v: number | null | undefined) {
  return v == null ? "—" : (v / 1e8).toFixed(2)
}

function pctCls(v: number | null | undefined) {
  return v == null ? "" : v > 0 ? "text-up" : v < 0 ? "text-down" : ""
}

function Segmented<T extends string | number>({
  value,
  options,
  onChange,
}: {
  value: T
  options: { key: T; label: string }[]
  onChange: (v: T) => void
}) {
  return (
    <div className="inline-flex rounded-md border border-border p-0.5">
      {options.map((o) => (
        <button
          key={o.key}
          type="button"
          onClick={() => onChange(o.key)}
          className={cn(
            "rounded px-2 py-0.5 text-xs transition-colors",
            value === o.key ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

function Section({ title, extra, children }: { title: string; extra?: ReactNode; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-border p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-medium">{title}</h2>
        {extra}
      </div>
      {children}
    </div>
  )
}

function StockSearch() {
  const navigate = useNavigate()
  const [q, setQ] = useState("")
  const [debounced, setDebounced] = useState("")
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 250)
    return () => clearTimeout(t)
  }, [q])
  const { data } = useQuery({
    queryKey: ["data-search", debounced],
    queryFn: () => searchStocks(debounced),
    enabled: debounced.length > 0,
  })
  return (
    <div className="relative max-w-md">
      <MagnifyingGlass className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="输入代码或名称：股票（含已退市）、ETF、可转债"
        className="pl-8"
      />
      {q && data && data.length > 0 && (
        <div className="absolute z-20 mt-1 max-h-80 w-full overflow-auto rounded-md border border-border bg-popover shadow-md">
          {data.map((s) => (
            <button
              key={s.code}
              type="button"
              onClick={() => {
                setQ("")
                navigate(`/app/data/${s.code}`)
              }}
              className="flex w-full items-center justify-between gap-3 px-3 py-1.5 text-left text-sm hover:bg-muted"
            >
              <span>
                <span className="font-mono tabular-nums text-muted-foreground">{s.code}</span> {s.name}
              </span>
              <span className="text-xs text-muted-foreground">
                {s.delistDate ? `已退市 ${s.delistDate}` : s.industry !== "—" ? s.industry : MARKET_LABEL[s.market]}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

const PRICE_RANGES = [
  { key: 250, label: "1 年" },
  { key: 750, label: "3 年" },
  { key: 1250, label: "5 年" },
  { key: 10000, label: "全部" },
]

function PriceSection({ code }: { code: string }) {
  const [limit, setLimit] = useState(750)
  const { data } = useQuery({ queryKey: ["data-kline", code, limit], queryFn: () => fetchKline(code, limit) })
  const points = useMemo(() => (data ?? []).map((b) => [b.t, b.close] as [string, number]), [data])
  return (
    <Section title="股价走势（前复权收盘价）" extra={<Segmented value={limit} options={PRICE_RANGES} onChange={setLimit} />}>
      <LineChart points={points} height={240} />
    </Section>
  )
}

function Overview({ p }: { p: StockProfile }) {
  const { info, quote, kline, indexes } = p
  const facts: [string, string][] = [
    ["市场", MARKET_LABEL[info.market] ?? info.market],
    ["行业", info.industry],
    ["上市日期", info.listDate ?? "—"],
    ["K 线覆盖", kline.count ? `${kline.first} ~ ${kline.last}（${kline.count} 根）` : "无"],
  ]
  if (quote) {
    facts.push(
      ["市盈率", num(quote.pe)],
      ["市净率", num(quote.pb)],
      ["总市值", quote.marketCap == null ? "—" : `${quote.marketCap.toFixed(0)} 亿`],
      ["股息率", quote.dividendYield == null ? "—" : `${quote.dividendYield.toFixed(2)}%`],
    )
  }
  return (
    <div className="rounded-lg border border-border p-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-lg font-semibold">{info.name}</h2>
        <span className="font-mono text-sm tabular-nums text-muted-foreground">{info.code}</span>
        {info.delistDate && <Badge variant="secondary">已退市 · {info.delistDate}</Badge>}
        {quote && (
          <span className="font-mono text-sm tabular-nums">
            {quote.price.toFixed(2)}{" "}
            <span className={pctCls(quote.changePct)}>
              {quote.changePct > 0 ? "+" : ""}
              {quote.changePct.toFixed(2)}%
            </span>
          </span>
        )}
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
        {facts.map(([k, v]) => (
          <div key={k}>
            <dt className="text-xs text-muted-foreground">{k}</dt>
            <dd className="font-mono tabular-nums">{v}</dd>
          </div>
        ))}
      </dl>
      {indexes.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
          <span className="text-muted-foreground">指数成分</span>
          {indexes.map((x) => (
            <Badge
              key={x.index}
              variant={x.current ? "default" : "secondary"}
              title={`首次 ${x.first}，最近 ${x.last}`}
            >
              {x.name}
              {x.current ? "" : `（${x.first.slice(0, 4)}–${x.last.slice(0, 4)}）`}
            </Badge>
          ))}
        </div>
      )}
    </div>
  )
}

function FinancialSection({ p }: { p: StockProfile }) {
  if (!p.financials.length) return <Section title="财务摘要"><p className="text-sm text-muted-foreground">暂无财报数据</p></Section>
  const profit = p.financials
    .filter((f) => f.reportDate.endsWith("12-31") && f.netProfit != null)
    .map((f) => [f.reportDate, f.netProfit! / 1e8] as [string, number])
    .reverse()
  return (
    <Section title="财务摘要（按报告期，含公告日）">
      {profit.length >= 2 && (
        <div className="mb-3">
          <p className="mb-1 text-xs text-muted-foreground">年度归母净利润（亿元）</p>
          <LineChart points={profit} height={160} zeroLine format={(v) => `${v.toFixed(2)} 亿`} />
        </div>
      )}
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>报告期</TableHead>
              <TableHead>公告日</TableHead>
              <TableHead className="text-right">营收（亿）</TableHead>
              <TableHead className="text-right">营收同比</TableHead>
              <TableHead className="text-right">归母净利（亿）</TableHead>
              <TableHead className="text-right">净利同比</TableHead>
              <TableHead className="text-right">EPS</TableHead>
              <TableHead className="text-right">ROE</TableHead>
              <TableHead className="text-right">毛利率</TableHead>
              <TableHead className="text-right">每股经营现金流</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {p.financials.map((f) => (
              <TableRow key={f.reportDate} className="font-mono text-xs tabular-nums">
                <TableCell>{f.reportDate}</TableCell>
                <TableCell className="text-muted-foreground">{f.noticeDate ?? "—"}</TableCell>
                <TableCell className="text-right">{yi(f.revenue)}</TableCell>
                <TableCell className={cn("text-right", pctCls(f.revenueYoy))}>{num(f.revenueYoy)}%</TableCell>
                <TableCell className="text-right">{yi(f.netProfit)}</TableCell>
                <TableCell className={cn("text-right", pctCls(f.profitYoy))}>{num(f.profitYoy)}%</TableCell>
                <TableCell className="text-right">{num(f.eps, 3)}</TableCell>
                <TableCell className="text-right">{num(f.roe)}%</TableCell>
                <TableCell className="text-right">{f.grossMargin == null ? "—" : `${f.grossMargin.toFixed(2)}%`}</TableCell>
                <TableCell className="text-right">{num(f.ocfPs, 3)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </Section>
  )
}

const STATEMENTS = [
  { key: "bs", label: "资产负债表" },
  { key: "is", label: "利润表" },
  { key: "cf", label: "现金流量表" },
] as const

function StatementSection({ p }: { p: StockProfile }) {
  const [kind, setKind] = useState<"bs" | "is" | "cf">("is")
  const [annual, setAnnual] = useState(false)
  const { periods, items } = p.statements
  const cols = useMemo(
    () => periods.map((d, i) => [d, i] as const).filter(([d]) => !annual || d.endsWith("12-31")).slice(0, 12),
    [periods, annual],
  )
  if (!periods.length) return null
  const rows = items.filter((it) => it.statement === kind)
  const fmt = (key: string, v: number | null) =>
    v == null ? "—" : key.endsWith("_ratio") ? `${v.toFixed(2)}%` : (v / 1e8).toFixed(2)
  return (
    <Section
      title="三大报表（主要科目，亿元）"
      extra={
        <div className="flex items-center gap-2">
          <Segmented
            value={annual ? "y" : "all"}
            onChange={(v) => setAnnual(v === "y")}
            options={[
              { key: "all", label: "全部报告期" },
              { key: "y", label: "仅年报" },
            ]}
          />
          <Segmented value={kind} onChange={setKind} options={STATEMENTS.map((x) => ({ ...x }))} />
        </div>
      }
    >
      {rows.length ? (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="sticky left-0 bg-background">科目</TableHead>
                {cols.map(([d]) => (
                  <TableHead key={d} className="text-right font-mono tabular-nums">{d}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((it) => (
                <TableRow key={it.key} className="text-xs">
                  <TableCell className="sticky left-0 whitespace-nowrap bg-background">{it.label}</TableCell>
                  {cols.map(([d, i]) => (
                    <TableCell key={d} className={cn("text-right font-mono tabular-nums", (it.values[i] ?? 0) < 0 && "text-down")}>
                      {fmt(it.key, it.values[i])}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">暂无数据</p>
      )}
      <p className="mt-2 text-[11px] text-muted-foreground">利润表、现金流量表为年初至报告期末的累计数；比率科目单位为 %。</p>
    </Section>
  )
}

function DividendSection({ p }: { p: StockProfile }) {
  return (
    <Section title="分红送转（每 10 股）">
      {p.dividends.length ? (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>报告期</TableHead>
                <TableHead>方案</TableHead>
                <TableHead className="text-right">派现（元）</TableHead>
                <TableHead className="text-right">送股</TableHead>
                <TableHead className="text-right">转增</TableHead>
                <TableHead>预案公告</TableHead>
                <TableHead>股权登记</TableHead>
                <TableHead>除权除息</TableHead>
                <TableHead>进度</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {p.dividends.map((d) => (
                <TableRow key={d.reportDate} className="text-xs">
                  <TableCell className="font-mono tabular-nums">{d.reportDate}</TableCell>
                  <TableCell className="max-w-[260px] truncate" title={d.plan}>{d.plan || "—"}</TableCell>
                  <TableCell className="text-right font-mono tabular-nums">{num(d.cashPer10)}</TableCell>
                  <TableCell className="text-right font-mono tabular-nums">{num(d.bonusPer10, 1)}</TableCell>
                  <TableCell className="text-right font-mono tabular-nums">{num(d.transferPer10, 1)}</TableCell>
                  <TableCell className="font-mono tabular-nums text-muted-foreground">{d.planNoticeDate ?? "—"}</TableCell>
                  <TableCell className="font-mono tabular-nums text-muted-foreground">{d.recordDate ?? "—"}</TableCell>
                  <TableCell className="font-mono tabular-nums">{d.exDate ?? "—"}</TableCell>
                  <TableCell>{d.progress}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">暂无分红记录</p>
      )}
    </Section>
  )
}

function wan(v: number | null | undefined) {
  if (v == null) return "—"
  return v >= 1e8 ? `${(v / 1e8).toFixed(2)} 亿` : `${(v / 1e4).toFixed(0)} 万`
}

function changeCls(v: string) {
  if (v === "新进" || v === "增加" || /^\d/.test(v)) return "text-up"
  if (v === "减少" || v.startsWith("-")) return "text-down"
  return "text-muted-foreground"
}

function changeLabel(v: string) {
  if (/^-?\d+(\.\d+)?$/.test(v)) {
    const n = Number(v)
    return n === 0 ? "不变" : `${n > 0 ? "+" : "-"}${wan(Math.abs(n))}`
  }
  return v || "—"
}

function CapitalSection({ p }: { p: StockProfile }) {
  const latest = p.capital[0]
  const counts = useMemo(() => p.holderCount.map((h) => [h.date, h.num] as [string, number]), [p.holderCount])
  if (!latest && !counts.length) return null
  return (
    <Section title="股本与股东户数">
      {latest && (
        <dl className="mb-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
          {([
            ["总股本", wan(latest.total)],
            ["流通 A 股", wan(latest.floatA)],
            ["限售股", wan(latest.limited)],
            ["最近变动", `${latest.date} · ${latest.reason || "—"}`],
          ] as [string, string][]).map(([k, v]) => (
            <div key={k}>
              <dt className="text-xs text-muted-foreground">{k}</dt>
              <dd className="font-mono tabular-nums">{v}</dd>
            </div>
          ))}
        </dl>
      )}
      {counts.length >= 2 && (
        <div className="mb-3">
          <p className="mb-1 text-xs text-muted-foreground">股东户数（户数下降通常意味着筹码集中）</p>
          <LineChart points={counts} height={160} format={(v) => `${Math.round(v).toLocaleString()} 户`} />
        </div>
      )}
      {p.capital.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-xs text-muted-foreground">股本变动记录（最近 {p.capital.length} 次）</summary>
          <div className="mt-2 overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>变动日</TableHead>
                  <TableHead>公告日</TableHead>
                  <TableHead>原因</TableHead>
                  <TableHead className="text-right">总股本</TableHead>
                  <TableHead className="text-right">流通 A 股</TableHead>
                  <TableHead className="text-right">限售股</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {p.capital.map((c) => (
                  <TableRow key={c.date} className="text-xs">
                    <TableCell className="font-mono tabular-nums">{c.date}</TableCell>
                    <TableCell className="font-mono tabular-nums text-muted-foreground">{c.noticeDate ?? "—"}</TableCell>
                    <TableCell>{c.reason || "—"}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{wan(c.total)}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{wan(c.floatA)}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{wan(c.limited)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </details>
      )}
    </Section>
  )
}

function HoldersSection({ p }: { p: StockProfile }) {
  const [kind, setKind] = useState<"all" | "float">("float")
  const t = p.topHolders[kind]
  if (!p.topHolders.all.items.length && !p.topHolders.float.items.length) return null
  return (
    <Section
      title={`十大${kind === "float" ? "流通" : ""}股东${t.endDate ? `（${t.endDate}）` : ""}`}
      extra={
        <Segmented
          value={kind}
          onChange={setKind}
          options={[
            { key: "float", label: "流通股东" },
            { key: "all", label: "全部股东" },
          ]}
        />
      }
    >
      {t.items.length ? (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">#</TableHead>
                <TableHead>股东</TableHead>
                <TableHead>股份类型</TableHead>
                <TableHead className="text-right">持股</TableHead>
                <TableHead className="text-right">{kind === "float" ? "占流通股" : "占总股本"}</TableHead>
                <TableHead className="text-right">较上期</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {t.items.map((h) => (
                <TableRow key={h.rank} className="text-xs">
                  <TableCell className="font-mono tabular-nums text-muted-foreground">{h.rank}</TableCell>
                  <TableCell className="max-w-[320px] truncate" title={h.name}>
                    {h.name}
                    {h.type && <span className="ml-1.5 text-muted-foreground">{h.type}</span>}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{h.sharesType || "—"}</TableCell>
                  <TableCell className="text-right font-mono tabular-nums">{wan(h.holdNum)}</TableCell>
                  <TableCell className="text-right font-mono tabular-nums">{h.ratio == null ? "—" : `${h.ratio.toFixed(2)}%`}</TableCell>
                  <TableCell className={cn("text-right font-mono tabular-nums", changeCls(h.change))}>{changeLabel(h.change)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {t.noticeDate && <p className="mt-2 text-[11px] text-muted-foreground">披露日 {t.noticeDate}</p>}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">暂无数据</p>
      )}
    </Section>
  )
}

function TradingSection({ p }: { p: StockProfile }) {
  const { margin, billboard, blocks } = p.trading
  const [view, setView] = useState<"margin" | "billboard" | "blocks">("margin")
  const rz = useMemo(
    () => margin.filter((m) => m.rzye != null).map((m) => [m.date, m.rzye! / 1e8] as [string, number]),
    [margin],
  )
  if (!margin.length && !billboard.length && !blocks.length) return null
  return (
    <Section
      title="交易数据"
      extra={
        <Segmented
          value={view}
          onChange={setView}
          options={[
            { key: "margin", label: "融资融券" },
            { key: "billboard", label: `龙虎榜（${billboard.length}）` },
            { key: "blocks", label: `大宗交易（${blocks.length}）` },
          ]}
        />
      }
    >
      {view === "margin" &&
        (rz.length >= 2 ? (
          <>
            <p className="mb-1 text-xs text-muted-foreground">
              融资余额（亿元，近一年）· 最新 {rz[rz.length - 1][1].toFixed(2)} 亿
              {margin[margin.length - 1].rqye != null && ` · 融券余额 ${wan(margin[margin.length - 1].rqye)}`}
            </p>
            <LineChart points={rz} height={180} format={(v) => `${v.toFixed(2)} 亿`} />
          </>
        ) : (
          <p className="text-sm text-muted-foreground">非两融标的或暂无数据</p>
        ))}
      {view === "billboard" &&
        (billboard.length ? (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>日期</TableHead>
                  <TableHead>上榜原因</TableHead>
                  <TableHead className="text-right">涨跌幅</TableHead>
                  <TableHead className="text-right">龙虎榜净买额</TableHead>
                  <TableHead className="text-right">占总成交</TableHead>
                  <TableHead>解读</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {billboard.map((b) => (
                  <TableRow key={`${b.date}-${b.reason}`} className="text-xs">
                    <TableCell className="font-mono tabular-nums">{b.date}</TableCell>
                    <TableCell className="max-w-[260px] truncate" title={b.reason}>{b.reason}</TableCell>
                    <TableCell className={cn("text-right font-mono tabular-nums", pctCls(b.changePct))}>{num(b.changePct)}%</TableCell>
                    <TableCell className={cn("text-right font-mono tabular-nums", pctCls(b.netAmt))}>{wan(b.netAmt)}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{num(b.dealRatio)}%</TableCell>
                    <TableCell className="text-muted-foreground">{b.comment || "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">暂无龙虎榜记录</p>
        ))}
      {view === "blocks" &&
        (blocks.length ? (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>日期</TableHead>
                  <TableHead className="text-right">成交价</TableHead>
                  <TableHead className="text-right">溢价率</TableHead>
                  <TableHead className="text-right">成交量</TableHead>
                  <TableHead className="text-right">成交额</TableHead>
                  <TableHead>买方营业部</TableHead>
                  <TableHead>卖方营业部</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {blocks.map((b, i) => (
                  <TableRow key={`${b.date}-${i}`} className="text-xs">
                    <TableCell className="font-mono tabular-nums">{b.date}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{num(b.price)}</TableCell>
                    <TableCell className={cn("text-right font-mono tabular-nums", pctCls(b.premiumPct))}>{num(b.premiumPct)}%</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{wan(b.volume)}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{wan(b.amount)}</TableCell>
                    <TableCell className="max-w-[200px] truncate" title={b.buyer}>{b.buyer || "—"}</TableCell>
                    <TableCell className="max-w-[200px] truncate" title={b.seller}>{b.seller || "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">暂无大宗交易</p>
        ))}
    </Section>
  )
}

function LockupSection({ p }: { p: StockProfile }) {
  if (!p.lockups.length) return null
  return (
    <Section title="限售解禁">
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>解禁日</TableHead>
              <TableHead>限售类型</TableHead>
              <TableHead className="text-right">解禁股数</TableHead>
              <TableHead className="text-right">实际可流通</TableHead>
              <TableHead className="text-right">解禁市值</TableHead>
              <TableHead className="text-right">占流通股</TableHead>
              <TableHead className="text-right">占总股本</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {p.lockups.map((x) => (
              <TableRow key={`${x.date}-${x.type}`} className={cn("text-xs", !x.upcoming && "text-muted-foreground")}>
                <TableCell className="font-mono tabular-nums">
                  {x.date}
                  {x.upcoming && <Badge variant="secondary" className="ml-1.5">待解禁</Badge>}
                </TableCell>
                <TableCell>{x.type || "—"}</TableCell>
                <TableCell className="text-right font-mono tabular-nums">{wan(x.shares)}</TableCell>
                <TableCell className="text-right font-mono tabular-nums">{wan(x.ableShares)}</TableCell>
                <TableCell className="text-right font-mono tabular-nums">{wan(x.marketCap)}</TableCell>
                <TableCell className="text-right font-mono tabular-nums">{x.floatRatio == null ? "—" : `${x.floatRatio.toFixed(2)}%`}</TableCell>
                <TableCell className="text-right font-mono tabular-nums">{x.totalRatio == null ? "—" : `${x.totalRatio.toFixed(2)}%`}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </Section>
  )
}

function AnnouncementSection({ code }: { code: string }) {
  const [page, setPage] = useState(1)
  const [open, setOpen] = useState<string | null>(null)
  const qc = useQueryClient()
  const key = ["data-ann", code, page]
  const { data, isLoading } = useQuery({ queryKey: key, queryFn: () => fetchAnnouncements(code, page) })
  const summarize = useMutation({
    mutationFn: summarizeAnnouncement,
    onSuccess: (item) => {
      qc.setQueryData<AnnouncementPage>(key, (old) =>
        old && { ...old, items: old.items.map((a) => (a.artCode === item.artCode ? item : a)) },
      )
      setOpen(item.artCode)
    },
  })
  const pages = data?.total ? Math.ceil(data.total / data.pageSize) : null
  return (
    <Section
      title="公告"
      extra={
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {data?.total != null && <span>共 {data.total} 条</span>}
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</Button>
          <span className="font-mono tabular-nums">{page}{pages ? ` / ${pages}` : ""}</span>
          <Button variant="outline" size="sm" disabled={pages != null && page >= pages} onClick={() => setPage(page + 1)}>下一页</Button>
        </div>
      }
    >
      {isLoading ? (
        <p className="text-sm text-muted-foreground">加载中…</p>
      ) : !data?.items.length ? (
        <p className="text-sm text-muted-foreground">暂无公告</p>
      ) : (
        <ul className="divide-y divide-border">
          {data.items.map((a) => {
            const busy = summarize.isPending && summarize.variables === a.artCode
            const failed = summarize.isError && summarize.variables === a.artCode
            return (
              <li key={a.artCode} className="py-2">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <a
                      href={`https://data.eastmoney.com/notices/detail/${code}/${a.artCode}.html`}
                      target="_blank"
                      rel="noreferrer"
                      className="text-sm hover:underline"
                    >
                      {a.title}
                    </a>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      <span className="font-mono tabular-nums">{a.date}</span>
                      {a.category && ` · ${a.category}`}
                      {" · "}
                      <a
                        href={`https://pdf.dfcfw.com/pdf/H2_${a.artCode}_1.pdf`}
                        target="_blank"
                        rel="noreferrer"
                        className="hover:text-foreground hover:underline"
                      >
                        PDF
                      </a>
                    </p>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="shrink-0 text-xs"
                    disabled={busy}
                    onClick={() => (a.summary ? setOpen(open === a.artCode ? null : a.artCode) : summarize.mutate(a.artCode))}
                  >
                    {busy ? "生成中…" : a.summary ? (open === a.artCode ? "收起摘要" : "查看 AI 摘要") : "生成 AI 摘要"}
                  </Button>
                </div>
                {failed && (
                  <p className="mt-1 text-xs text-destructive">
                    {summarize.error instanceof ApiError ? summarize.error.message : "生成失败"}
                  </p>
                )}
                {a.summary && open === a.artCode && (
                  <div className="mt-2 rounded-md bg-muted/50 px-3 py-2">
                    <Markdown>{a.summary}</Markdown>
                    <p className="mt-1 text-[11px] text-muted-foreground">AI 根据公告正文生成，仅供参考，以原文为准。</p>
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </Section>
  )
}

function SecurityView({ code }: { code: string }) {
  const navigate = useNavigate()
  const [years, setYears] = useState(5)
  const { data, isLoading } = useQuery({
    queryKey: ["data-security", code, years],
    queryFn: () => fetchSecurity(code, years),
    retry: false,
  })
  if (isLoading) return <p className="text-sm text-muted-foreground">加载中…</p>
  if (!data) return <p className="text-sm text-muted-foreground">没有找到 {code}</p>
  const facts: [string, string][] = [
    ["类型", data.type === "etf" ? "ETF" : "可转债"],
    ["市场", MARKET_LABEL[data.exchange] ?? data.exchange],
    ["上市日期", data.listDate ?? "未上市"],
  ]
  if (data.type === "cb") {
    facts.push(
      ["评级", data.rating ?? "—"],
      ["发行规模", data.issueSize == null ? "—" : `${data.issueSize.toFixed(2)} 亿`],
      ["到期日", data.maturityDate ?? "—"],
      ["初始转股价", num(data.convertPrice)],
    )
  }
  const last = data.points.length ? data.points[data.points.length - 1] : null
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-border p-4">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="text-lg font-semibold">{data.name}</h2>
          <span className="font-mono text-sm tabular-nums text-muted-foreground">{data.code}</span>
          {data.delistDate && <Badge variant="secondary">已退市 · {data.delistDate}</Badge>}
          {last && <span className="font-mono text-sm tabular-nums">{last[1].toFixed(3)}（{last[0]}）</span>}
          {data.stockCode && (
            <button type="button" className="text-sm underline" onClick={() => navigate(`/app/data/${data.stockCode}`)}>
              正股 {data.stockName ?? data.stockCode}
            </button>
          )}
        </div>
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
          {facts.map(([k, v]) => (
            <div key={k}>
              <dt className="text-xs text-muted-foreground">{k}</dt>
              <dd className="font-mono tabular-nums">{v}</dd>
            </div>
          ))}
        </dl>
      </div>
      <Section title="价格走势（不复权收盘价）" extra={<Segmented value={years} options={MACRO_YEARS} onChange={setYears} />}>
        <LineChart points={data.points} height={240} format={(v) => v.toFixed(3)} />
      </Section>
    </div>
  )
}

function StockView({ code }: { code: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["data-stock", code],
    queryFn: () => fetchStockProfile(code),
    retry: false,
  })
  if (isLoading) return <p className="text-sm text-muted-foreground">加载中…</p>
  // 股票池里没有的代码可能是 ETF / 可转债
  if (error instanceof ApiError && error.status === 404) return <SecurityView code={code} />
  if (error || !data) return <p className="text-sm text-muted-foreground">没有找到 {code}</p>
  return (
    <div className="space-y-4">
      <Overview p={data} />
      <PriceSection code={code} />
      <FinancialSection p={data} />
      <StatementSection p={data} />
      <DividendSection p={data} />
      <CapitalSection p={data} />
      <HoldersSection p={data} />
      <LockupSection p={data} />
      <TradingSection p={data} />
      <AnnouncementSection code={code} />
    </div>
  )
}

const MACRO_YEARS = [
  { key: 3, label: "3 年" },
  { key: 10, label: "10 年" },
  { key: 30, label: "全部" },
]

function fmtMacro(m: MacroCatalogItem, v: number | null) {
  if (v == null) return "—"
  if (m.unit === "%") return `${v.toFixed(2)}%`
  if (m.unit === "元") return `${(v / 1e8).toFixed(0)} 亿`
  return m.unit ? `${v.toFixed(2)} ${m.unit}` : v.toFixed(2)
}

function MacroView() {
  const { data: catalog } = useQuery({ queryKey: ["data-macro"], queryFn: fetchMacroCatalog })
  const [sel, setSel] = useState("cgb_10y")
  const [years, setYears] = useState(10)
  const { data: series } = useQuery({
    queryKey: ["data-macro", sel, years],
    queryFn: () => fetchMacroSeries(sel, years),
  })
  const cur = catalog?.find((m) => m.key === sel)
  const points = useMemo(() => series?.points ?? [], [series])
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-5">
        {(catalog ?? []).map((m) => {
          const delta = m.value != null && m.prev != null ? m.value - m.prev : null
          return (
            <button
              key={m.key}
              type="button"
              onClick={() => setSel(m.key)}
              className={cn(
                "rounded-lg border p-3 text-left transition-colors",
                sel === m.key ? "border-foreground/40 bg-muted/60" : "border-border hover:bg-muted/40",
              )}
            >
              <p className="text-xs text-muted-foreground">{m.name}</p>
              <p className="mt-1 font-mono text-base tabular-nums">{fmtMacro(m, m.value)}</p>
              <p className="mt-0.5 font-mono text-[11px] tabular-nums text-muted-foreground">
                {m.period ? (m.freq === "D" ? m.period : m.period.slice(0, 7)) : "—"}
                {delta != null && (
                  <span className={cn("ml-1.5", pctCls(delta))}>
                    {delta > 0 ? "+" : ""}
                    {m.unit === "元" ? `${(delta / 1e8).toFixed(0)} 亿` : delta.toFixed(2)}
                  </span>
                )}
              </p>
            </button>
          )
        })}
      </div>
      {cur && (
        <Section title={cur.name} extra={<Segmented value={years} options={MACRO_YEARS} onChange={setYears} />}>
          <LineChart
            points={points}
            height={260}
            zeroLine={cur.key.endsWith("_yoy")}
            format={(v) => fmtMacro(cur, v)}
          />
          <p className="mt-2 text-[11px] text-muted-foreground">
            数据来自东方财富数据中心；月度指标按所属月份标注，实际一般在次月中旬公布。
          </p>
        </Section>
      )}
    </div>
  )
}

function IndexView() {
  const navigate = useNavigate()
  const { data: catalog } = useQuery({ queryKey: ["data-index"], queryFn: fetchIndexCatalog })
  const [sel, setSel] = useState("000300")
  const [years, setYears] = useState(10)
  const { data } = useQuery({ queryKey: ["data-index", sel, years], queryFn: () => fetchIndexDetail(sel, years) })
  const hasWeight = data?.members.some((m) => m.weight != null)
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6">
        {(catalog ?? []).map((x) => (
          <button
            key={x.code}
            type="button"
            onClick={() => setSel(x.code)}
            className={cn(
              "rounded-lg border p-3 text-left transition-colors",
              sel === x.code ? "border-foreground/40 bg-muted/60" : "border-border hover:bg-muted/40",
            )}
          >
            <p className="text-xs text-muted-foreground">{x.name}</p>
            <p className="mt-1 font-mono text-base tabular-nums">{x.close == null ? "—" : x.close.toFixed(2)}</p>
            <p className={cn("mt-0.5 font-mono text-[11px] tabular-nums", pctCls(x.changePct))}>
              {x.changePct == null ? (x.date ?? "—") : `${x.changePct > 0 ? "+" : ""}${x.changePct.toFixed(2)}%`}
            </p>
          </button>
        ))}
      </div>
      {data && (
        <>
          <Section title={`${data.name} · 走势`} extra={<Segmented value={years} options={MACRO_YEARS} onChange={setYears} />}>
            <LineChart points={data.points} height={260} />
          </Section>
          <Section title={`成分股${data.snapshotDate ? `（${data.snapshotDate}，${data.members.length} 只）` : ""}`}>
            {data.members.length ? (
              <div className="max-h-[480px] overflow-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>代码</TableHead>
                      <TableHead>名称</TableHead>
                      <TableHead>行业</TableHead>
                      {hasWeight && <TableHead className="text-right">权重</TableHead>}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.members.map((m) => (
                      <TableRow
                        key={m.code}
                        className="cursor-pointer text-xs"
                        onClick={() => navigate(`/app/data/${m.code}`)}
                      >
                        <TableCell className="font-mono tabular-nums text-muted-foreground">{m.code}</TableCell>
                        <TableCell>{m.name}</TableCell>
                        <TableCell className="text-muted-foreground">{m.industry}</TableCell>
                        {hasWeight && (
                          <TableCell className="text-right font-mono tabular-nums">
                            {m.weight == null ? "—" : `${m.weight.toFixed(3)}%`}
                          </TableCell>
                        )}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">暂未收录该指数的成分</p>
            )}
            <p className="mt-2 text-[11px] text-muted-foreground">
              上证 50 / 沪深 300 / 中证 500 有 2005 年以来的历次调整；其余中证指数的成分与权重来自中证官网月度快照，从开始采集时积累。
            </p>
          </Section>
        </>
      )}
    </div>
  )
}

export function DataPage() {
  const { code } = useParams()
  const navigate = useNavigate()
  const [tab, setTab] = useState<"stock" | "index" | "macro">("stock")
  return (
    <div className="mx-auto max-w-[1600px] space-y-6 px-4 py-6 lg:px-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-foreground">数据</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            个股资料（含已退市）、财报与三大报表、分红、股本股东、解禁、两融龙虎榜大宗、指数成分、公告，以及宏观与利率序列
          </p>
        </div>
        <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
          <TabsList>
            <TabsTrigger value="stock">个股资料</TabsTrigger>
            <TabsTrigger value="index">指数</TabsTrigger>
            <TabsTrigger value="macro">宏观利率</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>
      {tab === "stock" ? (
        <div className="space-y-4">
          <StockSearch />
          {code ? (
            <StockView code={code} />
          ) : (
            <div className="rounded-lg border border-dashed border-border py-14 text-center text-sm text-muted-foreground">
              搜索一只股票查看资料，例如{" "}
              <button type="button" className="underline" onClick={() => navigate("/app/data/600519")}>贵州茅台</button>
              {" "}或已退市的{" "}
              <button type="button" className="underline" onClick={() => navigate("/app/data/600001")}>邯郸钢铁</button>
            </div>
          )}
        </div>
      ) : tab === "index" ? (
        <IndexView />
      ) : (
        <MacroView />
      )}
    </div>
  )
}
