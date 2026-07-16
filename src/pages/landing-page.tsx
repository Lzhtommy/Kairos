import { MarketingNav } from "@/components/marketing/nav"
import { Hero } from "@/components/marketing/hero"
import { DataCoverage } from "@/components/marketing/data-coverage"
import { FeatureBento } from "@/components/marketing/feature-bento"
import { HowItWorks } from "@/components/marketing/how-it-works"
import { StrategyShowcase } from "@/components/marketing/strategy-showcase"
import { StatsStrip } from "@/components/marketing/stats-strip"
import { FinalCta } from "@/components/marketing/final-cta"
import { MarketingFooter } from "@/components/marketing/footer"

export function LandingPage() {
  return (
    <div className="min-h-[100dvh]">
      <MarketingNav />
      <main>
        <Hero />
        <DataCoverage />
        <FeatureBento />
        <HowItWorks />
        <StrategyShowcase />
        <StatsStrip />
        <FinalCta />
      </main>
      <MarketingFooter />
    </div>
  )
}
