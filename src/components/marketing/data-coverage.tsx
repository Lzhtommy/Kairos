import { Bank, ChartBar, TrendUp, Money, ClockCountdown } from "@phosphor-icons/react"

const ITEMS = [
  { icon: Bank, label: "上交所 · 深交所 · 北交所全市场" },
  { icon: ChartBar, label: "60+ 财务与量价指标" },
  { icon: TrendUp, label: "陆股通资金流向" },
  { icon: Money, label: "融资融券数据" },
  { icon: ClockCountdown, label: "行情 2 秒级刷新" },
]

export function DataCoverage() {
  return (
    <section id="coverage" className="border-y border-border/60 bg-muted/30 py-8">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-center gap-x-8 gap-y-4 px-4 lg:px-6">
        {ITEMS.map((item) => (
          <div key={item.label} className="flex items-center gap-2 text-muted-foreground">
            <item.icon className="size-4 shrink-0 text-primary/80" />
            <span className="text-sm">{item.label}</span>
          </div>
        ))}
      </div>
    </section>
  )
}
