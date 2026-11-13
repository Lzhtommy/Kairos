import { motion, useReducedMotion } from "motion/react"
import { useShowcaseQuotes, changePct } from "@/lib/use-live-quotes"
import { ChangeBadge, PriceCell, Sparkline } from "@/components/market/price-change"
import { CodeBlock } from "@/components/strategy/code-block"
import {
  BellRinging,
  ChartPieSlice,
  Database,
  FunnelSimple,
  Sparkle,
  Stethoscope,
} from "@phosphor-icons/react"
import type { ReactNode } from "react"
import { cn } from "@/lib/utils"

// 与策略工坊真实生成的代码同一格式（strategy_ai._render_code）
const SNIPPET = `def screen(stock):
    return (
        stock.pe < industry_median(stock)  # 市盈率
        and stock.roe >= 12.0  # ROE(%)
        and vol_surge(stock, window=20, ratio=1.8)
    )`

const FILTER_CHIPS = ["PE 低于行业中位", "ROE ≥ 12%", "股息率 ≥ 3%", "行业内 PB 后 20%"]

const SECTOR_METRICS = ["RRG 轮动图", "估值分位", "拥挤度", "每日叙事"]

// 与「数据」页的个股资料 / 指数 / 宏观利率三个分栏一致，增删数据集后记得同步
const DATA_GROUPS = [
  { label: "行情与指标", items: "上市以来日 K · 每日 PE/PB、市值、换手 · 历史 ST 与停牌" },
  { label: "财务", items: "资产负债表、利润表、现金流量表 · 历年分红" },
  { label: "股本与股东", items: "股本变动 · 股东户数 · 十大股东 · 限售解禁" },
  { label: "交易", items: "融资融券 · 龙虎榜 · 大宗交易" },
  { label: "公告", items: "标题直达原文与 PDF · 按需生成 AI 摘要" },
  { label: "市场", items: "11 个主要指数与成分 · ETF、可转债 · 宏观与利率" },
]

const CHECKUP_DIMENSIONS = ["盈利", "成长", "财务健康", "现金流", "估值", "股东回报", "公告事件"]

function Cell({
  className,
  index,
  children,
}: {
  className?: string
  index: number
  children: ReactNode
}) {
  const reduce = useReducedMotion()
  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 20 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.25 }}
      transition={{ duration: 0.55, delay: index * 0.06, ease: [0.16, 1, 0.3, 1] }}
      className={cn("rounded-2xl border border-border p-6", className)}
    >
      {children}
    </motion.div>
  )
}

function CellTitle({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      {icon}
      <h3 className="text-lg font-medium text-foreground">{children}</h3>
    </div>
  )
}

export function FeatureBento() {
  const { stocks } = useShowcaseQuotes(6)
  const rows = stocks.slice(2, 6)

  return (
    <section id="features" className="mx-auto max-w-7xl px-4 py-20 lg:px-6">
      <h2 className="max-w-xl text-3xl font-semibold tracking-tight text-foreground md:text-4xl">
        从选股到验证，一条链路走完
      </h2>

      <div className="mt-10 grid grid-cols-1 gap-4 md:grid-cols-6">
        {/* 选股：实时行情 + 条件 */}
        <Cell index={0} className="flex flex-col bg-card md:col-span-4 md:row-span-2">
          <CellTitle icon={<FunnelSimple className="size-4 text-primary" />}>全市场选股</CellTitle>
          <p className="mt-1.5 text-sm text-muted-foreground">
            按估值、盈利、量价条件筛选，也可以直接套用你保存的策略。
          </p>
          <div className="mt-5 flex flex-wrap gap-2">
            {FILTER_CHIPS.map((chip) => (
              <span
                key={chip}
                className="rounded-full border border-border bg-muted/40 px-3 py-1 text-xs text-foreground"
              >
                {chip}
              </span>
            ))}
          </div>
          <div className="mt-5 flex-1 divide-y divide-border/60 rounded-xl border border-border/60 bg-muted/20 px-4">
            {rows.length === 0
              ? Array.from({ length: 4 }, (_, i) => (
                  <div key={i} className="flex items-center gap-3 py-3">
                    <div className="h-8 flex-1 animate-pulse rounded bg-muted" />
                  </div>
                ))
              : rows.map((s) => (
                  <div key={s.code} className="flex items-center gap-3 py-2.5 first:pt-3 last:pb-3">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-foreground">{s.name}</p>
                      <p className="font-mono text-[11px] text-muted-foreground">{s.code}</p>
                    </div>
                    <Sparkline data={s.spark} changePct={changePct(s)} className="hidden sm:block" />
                    <PriceCell price={s.price} changePct={changePct(s)} className="w-14 text-right text-sm" />
                    <ChangeBadge pct={changePct(s)} className="w-16 justify-center" />
                  </div>
                ))}
          </div>
        </Cell>

        {/* 行业轮动 */}
        <Cell index={1} className="bg-primary/[0.06] md:col-span-2">
          <CellTitle icon={<ChartPieSlice className="size-4 text-primary" />}>行业轮动</CellTitle>
          <p className="mt-1.5 text-sm text-muted-foreground">
            申万 31 个一级行业：轮动图看强弱切换，估值对照自身近 5 年分位看贵贱，拥挤过热先预警。
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            {SECTOR_METRICS.map((m) => (
              <span key={m} className="rounded-full bg-background/70 px-2.5 py-1 text-xs text-foreground">
                {m}
              </span>
            ))}
          </div>
        </Cell>

        {/* 个股基本面体检 */}
        <Cell index={2} className="bg-muted/40 md:col-span-2">
          <CellTitle icon={<Stethoscope className="size-4 text-primary" />}>个股基本面体检</CellTitle>
          <p className="mt-1.5 text-sm text-muted-foreground">
            七个维度按固定规则打分，AI 只负责解读，数据不足直接标注。
          </p>
          <div className="mt-4 flex flex-wrap gap-1.5">
            {CHECKUP_DIMENSIONS.map((d) => (
              <span key={d} className="rounded-full border border-border bg-background px-2.5 py-0.5 text-xs text-foreground">
                {d}
              </span>
            ))}
          </div>
        </Cell>

        {/* AI 生成策略 */}
        <Cell index={3} className="bg-card md:col-span-3">
          <CellTitle icon={<Sparkle weight="fill" className="size-4 text-primary" />}>
            对话生成策略
          </CellTitle>
          <p className="mt-1.5 text-sm text-muted-foreground">
            说出选股逻辑，AI 生成可查看、可修改、可回测的策略代码。
          </p>
          <div className="mt-5 rounded-xl border border-border/60 bg-muted/20 p-3">
            <CodeBlock code={SNIPPET} className="overflow-x-auto" />
          </div>
        </Cell>

        {/* 盘后推送 */}
        <Cell index={4} className="flex flex-col bg-primary/[0.06] md:col-span-3">
          <CellTitle icon={<BellRinging className="size-4 text-primary" />}>
            盘后自动运行，有变化才提醒
          </CellTitle>
          <p className="mt-1.5 text-sm text-muted-foreground">
            每个交易日 15:25 跑一遍你的策略，命中有新进或调出时推送到企业微信、飞书或邮箱。
          </p>
          <figure className="mt-5 rounded-xl border border-border/60 bg-background p-4 shadow-sm shadow-primary/5">
            <p className="text-sm font-medium text-foreground">Kairos 策略日报</p>
            <p className="mt-1.5 text-sm text-muted-foreground">
              「低估值高股息」命中 37 只：新进 4，调出 2
            </p>
            <figcaption className="mt-3 text-[11px] text-muted-foreground">通知示例</figcaption>
          </figure>
        </Cell>

        {/* 数据中心 */}
        <Cell index={5} className="bg-card md:col-span-6">
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,2.2fr)] lg:gap-10">
            <div>
              <CellTitle icon={<Database className="size-4 text-primary" />}>数据中心</CellTitle>
              <p className="mt-1.5 text-sm text-muted-foreground">
                一只股票的资料一页看全，已退市的也查得到。全部来自公开数据源，盘后自动更新。
              </p>
            </div>
            <dl className="grid grid-cols-1 gap-x-8 gap-y-4 sm:grid-cols-2 lg:grid-cols-3">
              {DATA_GROUPS.map((g) => (
                <div key={g.label} className="border-t border-border/60 pt-3">
                  <dt className="text-xs font-medium text-foreground">{g.label}</dt>
                  <dd className="mt-1 text-xs leading-relaxed text-muted-foreground">{g.items}</dd>
                </div>
              ))}
            </dl>
          </div>
        </Cell>
      </div>
    </section>
  )
}
