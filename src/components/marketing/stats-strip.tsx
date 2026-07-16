const STATS = [
  { value: "5000+", label: "只 A 股实时覆盖" },
  { value: "60+", label: "量价与财务指标" },
  { value: "2秒", label: "行情刷新间隔" },
  { value: "3", label: "大交易所统一接入" },
]

export function StatsStrip() {
  return (
    <section className="border-y border-border/60 py-14">
      <div className="mx-auto grid max-w-7xl grid-cols-2 gap-8 px-4 lg:grid-cols-4 lg:px-6">
        {STATS.map((s) => (
          <div key={s.label}>
            <p className="font-mono text-3xl font-semibold tabular-nums text-foreground md:text-4xl">
              {s.value}
            </p>
            <p className="mt-1 text-sm text-muted-foreground">{s.label}</p>
          </div>
        ))}
      </div>
    </section>
  )
}
