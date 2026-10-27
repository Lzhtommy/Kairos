import { motion, useReducedMotion } from "motion/react"
import { ChatText, Code, ChartLineUp, Robot } from "@phosphor-icons/react"

const STEPS = [
  {
    icon: ChatText,
    title: "描述选股逻辑",
    body: "一句话说清想法，比如「放量突破年线且换手率不高」。",
  },
  {
    icon: Code,
    title: "生成策略代码",
    body: "AI 转成结构化的策略，条件和参数都能直接查看、修改。",
  },
  {
    icon: ChartLineUp,
    title: "按真实约束回测",
    body: "涨跌停买不进、换手按实际计费、只用当时可得的数据，回测完 AI 给出诊断和改进建议。",
  },
  {
    icon: Robot,
    title: "模拟盘跟单检验",
    body: "盘后信号次日开盘买入，和回测同一套成交规则，看策略在样本外的真实表现。",
  },
]

export function HowItWorks() {
  const reduce = useReducedMotion()
  return (
    <section id="workflow" className="border-t border-border/60 bg-muted/20 py-20">
      <div className="mx-auto max-w-7xl px-4 lg:px-6">
        <h2 className="max-w-lg text-3xl font-semibold tracking-tight text-foreground md:text-4xl">
          从一句话到经得起检验的策略
        </h2>
        <ol className="mt-10 grid grid-cols-1 gap-x-8 gap-y-8 md:grid-cols-2 lg:grid-cols-4">
          {STEPS.map((step, i) => (
            <motion.li
              key={step.title}
              initial={reduce ? false : { opacity: 0, y: 16 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, amount: 0.4 }}
              transition={{ duration: 0.5, delay: i * 0.08, ease: [0.16, 1, 0.3, 1] }}
              className="border-t-2 border-primary/30 pt-5"
            >
              <step.icon className="size-5 text-primary" weight="duotone" />
              <h3 className="mt-4 text-base font-medium text-foreground">{step.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{step.body}</p>
            </motion.li>
          ))}
        </ol>
      </div>
    </section>
  )
}
