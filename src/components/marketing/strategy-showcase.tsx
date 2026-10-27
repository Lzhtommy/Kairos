import { Check, Sparkle } from "@phosphor-icons/react"
import { CodeBlock } from "@/components/strategy/code-block"

// 真实生成格式（strategy_ai._render_code），示例来自策略工坊的一次对话
const CODE = `def screen(stock):
    """由自然语言描述生成的选股策略"""
    return (
        stock.pe < 30.0  # 市盈率
        and stock.market_cap > 100.0  # 总市值(亿)
        and daily_change(stock, days=3, min=0.0, max=100.0)
        and vol_surge(stock, window=20, ratio=2.0)
        and macd_cross(stock, fast=12, slow=26, signal=9)
    )

# 调仓频率: monthly_first_trading_day
# 费率: 双边 0.050%`

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
                连续 3 日放量上涨，PE 低于 30，市值大于 100 亿，MACD 金叉。
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
            年线、放量、金叉、行业内估值排名，这些日常说法不用翻译成术语，Kairos 直接理解并生成策略。
          </p>
          <ul className="mt-6 space-y-3 text-sm text-foreground">
            {[
              "估值、财务、量价与均线、MACD、RSI 等技术形态自由组合",
              "支持全市场或行业内排名，以及多因子综合打分",
              "保存后一键回测，也能交给模拟盘持续跟踪",
            ].map((item) => (
              <li key={item} className="flex gap-2.5">
                <Check className="mt-0.5 size-4 shrink-0 text-primary" />
                {item}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  )
}
