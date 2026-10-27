import { motion, useReducedMotion } from "motion/react"
import { buttonVariants } from "@/components/ui/button"
import { HeroPreview } from "@/components/marketing/hero-preview"
import { PrimaryCta } from "@/components/marketing/primary-cta"
import { cn } from "@/lib/utils"

export function Hero() {
  const reduce = useReducedMotion()

  return (
    <section className="mx-auto max-w-7xl px-4 pt-16 pb-20 lg:px-6 lg:pt-24 lg:pb-28">
      <div className="grid grid-cols-1 items-center gap-12 lg:grid-cols-[1fr_0.9fr] lg:gap-8">
        <motion.div
          initial={reduce ? false : { opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
        >
          <h1 className="text-4xl font-semibold tracking-tight text-foreground md:text-5xl lg:text-6xl">
            把选股逻辑说出来
            <br />
            剩下的交给 Kairos
          </h1>
          <p className="mt-5 max-w-md text-base leading-relaxed text-muted-foreground">
            从选股、个股体检到回测和模拟盘，用一句话把想法变成能验证的策略。
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <PrimaryCta />
            <a href="#features" className={cn(buttonVariants({ variant: "outline", size: "lg" }), "px-5")}>
              查看功能
            </a>
          </div>
        </motion.div>
        <motion.div
          initial={reduce ? false : { opacity: 0, y: 24, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          transition={{ duration: 0.7, delay: 0.15, ease: [0.16, 1, 0.3, 1] }}
        >
          <HeroPreview />
        </motion.div>
      </div>
    </section>
  )
}
