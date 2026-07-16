import { cn } from "@/lib/utils"

export function Logo({ className }: { className?: string }) {
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden>
        <path
          d="M12 2 L14.2 9.6 L22 12 L14.2 14.4 L12 22 L9.8 14.4 L2 12 L9.8 9.6 Z"
          className="fill-primary"
        />
      </svg>
      <span className="text-[15px] font-semibold tracking-tight text-foreground">Kairos</span>
    </div>
  )
}
