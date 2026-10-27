import { Link } from "react-router-dom"
import { ArrowRight } from "@phosphor-icons/react"
import { buttonVariants } from "@/components/ui/button"
import { useAuth } from "@/lib/auth"
import { cn } from "@/lib/utils"

/** 营销页唯一的主行动按钮：未登录去邀请码注册，已登录直接进应用。全页共用，文案保持一致。 */
export function PrimaryCta({
  size = "lg",
  className,
}: {
  size?: "sm" | "lg"
  className?: string
}) {
  const { user } = useAuth()
  return (
    <Link
      to={user ? "/app" : "/login?mode=register"}
      className={cn(buttonVariants({ size }), "gap-2 active:translate-y-px", size === "lg" && "px-5", className)}
    >
      {user ? "进入应用" : "邀请码注册"}
      {size === "lg" && <ArrowRight className="size-4" />}
    </Link>
  )
}
