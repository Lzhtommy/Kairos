// 数字均来自线上实际数据与调度配置，改动功能后记得同步
const STATS = [
  { value: "5500+", label: "只 A 股全覆盖" },
  { value: "31", label: "个申万一级行业" },
  { value: "7", label: "个基本面体检维度" },
  { value: "15:25", label: "每个交易日盘后自动运行" },
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
