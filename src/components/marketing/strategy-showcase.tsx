import { Sparkle } from "@phosphor-icons/react"
import { CodeBlock } from "@/components/strategy/code-block"

const CODE = `def screen(stock):
    """连续放量突破年线"""
    above_ma250 = stock.price > stock.ma(250)
    volume_up = stock.volume > stock.volume_ma(20) * 1.8
    low_turnover = stock.turnover_rate < 0.08

    return above_ma250 and volume_up and low_turnover
`

export function StrategyShowcase() {
  return (
    <section id="strategy" className="mx-auto max-w-7xl px-4 py-20 lg:px-6">
      <div className="grid grid-cols-1 items-center gap-10 lg:grid-cols-2 lg:gap-16">
        <div className="order-2 lg:order-1">
          <div className="rounded-2xl border border-border bg-card p-5">
            <div className="flex gap-3">
              <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/15">
                <Sparkle weight="fill" className="size-3.5 text-primary" />
              </div>
              <div className="rounded-xl bg-muted px-3.5 py-2.5 text-sm leading-relaxed text-foreground">
                股价站上年线，成交量放大到20日均量的1.8倍以上，同时换手率控制在8%以内，避免追高。
              </div>
            </div>
            <div className="mt-4 rounded-xl border border-border/60 bg-muted/20 p-3.5">
              <CodeBlock code={CODE} className="overflow-x-auto" />
            </div>
          </div>
        </div>
        <div className="order-1 lg:order-2">
          <h2 className="text-3xl font-semibold tracking-tight text-foreground md:text-4xl">
            AI 听得懂中国股民的话
          </h2>
          <p className="mt-4 text-base leading-relaxed text-muted-foreground">
            年线、换手率、放量突破、北向资金，这些说法不需要翻译成技术术语，Kairos 直接理解并生成对应的策略代码。
          </p>
          <ul className="mt-6 space-y-3 text-sm text-foreground">
            {[
              "支持均线、量价、财务、资金流等多维度指标组合",
              "生成的代码可直接查看、修改与复用",
              "每条策略都能一键回测，看清历史表现再使用",
            ].map((item) => (
              <li key={item} className="flex gap-2.5">
                <span className="mt-2 size-1 shrink-0 rounded-full bg-primary" />
                {item}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  )
}
