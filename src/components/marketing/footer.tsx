import { Logo } from "@/components/layout/logo"

export function MarketingFooter() {
  return (
    <footer className="border-t border-border/60 py-10">
      <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-4 px-4 sm:flex-row lg:px-6">
        <Logo />
        <p className="text-xs text-muted-foreground">
          © 2026 Kairos. 数据仅供参考，不构成投资建议。
          {" · "}
          <a
            href="https://beian.miit.gov.cn/"
            target="_blank"
            rel="noreferrer"
            className="hover:text-foreground"
          >
            粤ICP备2026044175号
          </a>
        </p>
      </div>
    </footer>
  )
}
