import { Bank, ChartPieSlice, FileText, ChartLine, ClockCountdown } from "@phosphor-icons/react"

const ITEMS = [
  { icon: Bank, label: "沪深北三所全市场" },
  { icon: ChartPieSlice, label: "申万一级行业估值分位" },
  { icon: FileText, label: "年报、半年报、季报深读" },
  { icon: ChartLine, label: "历史日 K 线回测" },
  { icon: ClockCountdown, label: "盘中行情分钟级更新" },
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
