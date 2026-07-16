import type { IndexQuote } from "@/lib/mock-data"
import { cn } from "@/lib/utils"

function tone(v: number) {
  if (v > 0) return "text-up"
  if (v < 0) return "text-down"
  return "text-muted-foreground"
}

export function IndexStrip({ indices }: { indices: IndexQuote[] }) {
  return (
    <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-3 lg:grid-cols-5">
      {indices.map((idx) => (
        <div key={idx.code} className="bg-card px-4 py-3">
          <p className="text-xs text-muted-foreground">{idx.name}</p>
          <p className={cn("mt-1 font-mono text-lg font-semibold tabular-nums", tone(idx.change))}>
            {idx.price.toFixed(2)}
          </p>
          <p className={cn("font-mono text-xs tabular-nums", tone(idx.change))}>
            {idx.change > 0 ? "+" : ""}
            {idx.change.toFixed(2)} ({idx.changePct > 0 ? "+" : ""}
            {idx.changePct.toFixed(2)}%)
          </p>
        </div>
      ))}
    </div>
  )
}
