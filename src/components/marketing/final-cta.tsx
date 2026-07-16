import { Link } from "react-router-dom"
import { ArrowRight } from "@phosphor-icons/react"
import { buttonVariants } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export function FinalCta() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-24 text-center lg:px-6">
      <h2 className="mx-auto max-w-2xl text-3xl font-semibold tracking-tight text-foreground md:text-4xl">
        下一次操作前，先把逻辑想清楚
      </h2>
      <p className="mx-auto mt-4 max-w-md text-base text-muted-foreground">
        现在开始使用 Kairos，用对话生成属于你自己的选股策略。
      </p>
      <div className="mt-8">
        <Link to="/app" className={cn(buttonVariants({ size: "lg" }), "gap-2 px-6")}>
          开始使用
          <ArrowRight className="size-4" />
        </Link>
      </div>
    </section>
  )
}
