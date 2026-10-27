import { PrimaryCta } from "@/components/marketing/primary-cta"

export function FinalCta() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-24 text-center lg:px-6">
      <h2 className="mx-auto max-w-2xl text-3xl font-semibold tracking-tight text-foreground md:text-4xl">
        下一次操作前，先把逻辑想清楚
      </h2>
      <p className="mx-auto mt-4 max-w-md text-base text-muted-foreground">
        Kairos 目前采用邀请制，凭邀请码注册即可使用全部功能。
      </p>
      <div className="mt-8 flex justify-center">
        <PrimaryCta />
      </div>
    </section>
  )
}
