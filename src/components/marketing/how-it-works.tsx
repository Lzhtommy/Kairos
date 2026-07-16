import { ChatText, Code, ChartLineUp } from "@phosphor-icons/react"

const STEPS = [
  {
    icon: ChatText,
    title: "描述你的选股逻辑",
    body: "用一句话说清楚想法，比如「放量突破年线且换手率不高」。",
  },
  {
    icon: Code,
    title: "AI 生成可运行代码",
    body: "自动转换为结构化策略代码，逻辑清晰、参数可调。",
  },
  {
    icon: ChartLineUp,
    title: "回测验证再上线",
    body: "先看历史表现，确认逻辑站得住脚，再应用到实盘筛选。",
  },
]

export function HowItWorks() {
  return (
    <section className="border-t border-border/60 bg-muted/20 py-20">
      <div className="mx-auto max-w-7xl px-4 lg:px-6">
        <h2 className="max-w-lg text-3xl font-semibold tracking-tight text-foreground md:text-4xl">
          从一句话到一个可回测的策略
        </h2>
        <div className="mt-10 grid grid-cols-1 divide-y divide-border/60 md:grid-cols-3 md:divide-x md:divide-y-0">
          {STEPS.map((step) => (
            <div key={step.title} className="py-6 first:pt-0 md:py-0 md:px-8 md:first:pl-0 md:last:pr-0">
              <step.icon className="size-5 text-primary" weight="duotone" />
              <h3 className="mt-4 text-base font-medium text-foreground">{step.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{step.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
