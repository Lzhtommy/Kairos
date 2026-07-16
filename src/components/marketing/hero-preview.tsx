import { useLiveQuotes, changePct } from "@/lib/use-live-quotes"
import { ChangeBadge, PriceCell, Sparkline } from "@/components/market/price-change"
import { Sparkle } from "@phosphor-icons/react"

export function HeroPreview() {
  const { stocks, indices } = useLiveQuotes(2600)
  const rows = stocks.slice(0, 5)
  const idx = indices[0]

  return (
    <div className="relative rounded-2xl border border-white/10 bg-card/80 p-1.5 shadow-2xl shadow-black/20 backdrop-blur">
      <div className="rounded-xl border border-border/60 bg-background p-4">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <p className="text-xs text-muted-foreground">{idx.name}</p>
            <p className="font-mono text-xl font-semibold tabular-nums text-up">
              {idx.price.toFixed(2)}
              <span className="ml-2 text-sm">
                {idx.change > 0 ? "+" : ""}
                {idx.changePct.toFixed(2)}%
              </span>
            </p>
          </div>
          <div className="flex items-center gap-1.5 rounded-full bg-accent px-2.5 py-1 text-xs font-medium text-accent-foreground">
            <Sparkle weight="fill" className="size-3" />
            策略选股中
          </div>
        </div>
        <div className="divide-y divide-border/60">
          {rows.map((s) => (
            <div key={s.code} className="flex items-center gap-3 py-2 first:pt-0 last:pb-0">
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
    </div>
  )
}
