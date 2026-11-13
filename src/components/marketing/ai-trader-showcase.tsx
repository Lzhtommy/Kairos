import { Check, Robot } from "@phosphor-icons/react"

// 时间来自调度配置（main.py 的 agent_premarket / agent_postclose / agent_review），改动后记得同步
const RHYTHM = [
  {
    time: "08:50",
    title: "盘前分析",
    body: "查行情、板块、公告和新闻，必要时联网搜索，然后下当天的单。",
  },
  {
    time: "15:45",
    title: "盘后复盘",
    body: "结算当日成交，对照盘前计划，把可复用的教训记进经验库。",
  },
  {
    time: "周六 10:00",
    title: "周度反思",
    body: "按实际收益给每条经验打分，淘汰被证伪的，提交新版交易手册。",
  },
]

export function AiTraderShowcase() {
  return (
    <section id="agent" className="border-t border-border/60">
      <div className="mx-auto grid max-w-7xl grid-cols-1 items-center gap-10 px-4 py-20 lg:grid-cols-2 lg:gap-16 lg:px-6">
        <div>
          <h2 className="text-3xl font-semibold tracking-tight text-foreground md:text-4xl">
            看 AI 自己管一个模拟账户
          </h2>
          <p className="mt-4 text-base leading-relaxed text-muted-foreground">
            AI 操盘手每个交易日自己调研、自己下单，按真实的交易规则和费率记账。持仓、净值、每笔订单和完整的决策过程，登录后都能看。
          </p>
          <ul className="mt-6 space-y-3 text-sm text-foreground">
            {[
              "T+1、100 股一手、一字涨停买不进，佣金和印花税照收",
              "下单时写明依据的经验，事后按这些订单的实际收益检验",
              "交易手册由它自己每周改写，历史版本全部保留",
            ].map((item) => (
              <li key={item} className="flex gap-2.5">
                <Check className="mt-0.5 size-4 shrink-0 text-primary" />
                {item}
              </li>
            ))}
          </ul>
          <p className="mt-6 text-xs text-muted-foreground">
            模拟账户，不涉及真实资金，不构成投资建议。
          </p>
        </div>
        <div className="rounded-2xl border border-border bg-card p-5">
          <div className="flex items-center gap-2">
            <Robot weight="fill" className="size-4 text-primary" />
            <p className="text-sm font-medium text-foreground">运行节奏</p>
          </div>
          <ol className="mt-4 divide-y divide-border/60">
            {RHYTHM.map((step) => (
              <li key={step.title} className="flex gap-4 py-3.5 first:pt-0 last:pb-0">
                <span className="w-20 shrink-0 pt-0.5 font-mono text-xs tabular-nums text-primary">
                  {step.time}
                </span>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-foreground">{step.title}</p>
                  <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{step.body}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </section>
  )
}
