import { useShowcaseQuotes, changePct } from "@/lib/use-live-quotes"
import { ChangeBadge, PriceCell, Sparkline } from "@/components/market/price-change"
import { CodeBlock } from "@/components/strategy/code-block"
import { FunnelSimple, Sparkle } from "@phosphor-icons/react"

const SNIPPET = `def screen(stock):
    pe_ok = stock.pe < median_pe
    return pe_ok and stock.roe >= 0.12`

export function FeatureBento() {
  const { stocks } = useShowcaseQuotes(6)
  const rows = stocks.slice(2, 6)

  return (
    <section id="features" className="mx-auto max-w-7xl px-4 py-20 lg:px-6">
      <div className="max-w-xl">
        <h2 className="text-3xl font-semibold tracking-tight text-foreground md:text-4xl">
          三个核心能力，覆盖从看盘到验证的全过程
        </h2>
      </div>

      <div className="mt-10 grid grid-cols-1 gap-4 md:grid-cols-2 md:grid-rows-2">
        {/* Large cell: real-time quotes */}
        <div className="rounded-2xl border border-border bg-card p-6 md:row-span-2">
          <h3 className="text-lg font-medium text-foreground">全市场实时行情</h3>
          <p className="mt-1.5 text-sm text-muted-foreground">
            沪深北三所股票统一展示，价格、涨跌与走势秒级更新。
          </p>
          <div className="mt-5 divide-y divide-border/60 rounded-xl border border-border/60 bg-muted/20 px-4">
            {rows.map((s) => (
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
        </div>

        {/* Filtering */}
        <div className="rounded-2xl border border-border bg-card p-6">
          <div className="flex items-center gap-2">
            <FunnelSimple className="size-4 text-primary" />
            <h3 className="text-lg font-medium text-foreground">传统 + 策略双重筛选</h3>
          </div>
          <p className="mt-1.5 text-sm text-muted-foreground">
            按 PE、ROE、行业等指标筛选，也可以直接套用已保存的策略。
          </p>
          <div className="mt-5 flex flex-wrap gap-2">
            {["PE 8-35", "ROE ≥ 12%", "股息率 ≥ 3%", "白酒 / 银行"].map((chip) => (
              <span
                key={chip}
                className="rounded-full border border-border bg-muted/40 px-3 py-1 font-mono text-xs text-foreground"
              >
                {chip}
              </span>
            ))}
          </div>
        </div>

        {/* AI strategy generation */}
        <div className="rounded-2xl border border-border bg-card p-6">
          <div className="flex items-center gap-2">
            <Sparkle weight="fill" className="size-4 text-primary" />
            <h3 className="text-lg font-medium text-foreground">对话生成策略代码</h3>
          </div>
          <p className="mt-1.5 text-sm text-muted-foreground">
            用自然语言描述逻辑，AI 直接生成可运行、可回测的策略。
          </p>
          <div className="mt-5 rounded-xl border border-border/60 bg-muted/20 p-3">
            <CodeBlock code={SNIPPET} className="overflow-x-auto" />
          </div>
        </div>
      </div>
    </section>
  )
}
