import { useEffect, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Robot, ArrowCounterClockwise } from "@phosphor-icons/react"
import { BacktestChart } from "@/components/strategy/backtest-chart"
import { Switch } from "@/components/ui/switch"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import {
  fetchPaperAccount,
  fetchPaperAccounts,
  resetPaper,
  togglePaper,
  type PaperAccount,
} from "@/api/paper"

const REASON_LABELS: Record<string, string> = {
  hold: "持有到期",
  signal: "反向信号",
  stop_gain: "止盈",
  stop_loss: "止损",
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "up" | "down" }) {
  return (
    <div className="rounded-lg border border-border p-3.5">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p
        className={cn(
          "mt-1 font-mono text-lg font-semibold tabular-nums",
          tone === "up" && "text-up",
          tone === "down" && "text-down",
        )}
      >
        {value}
      </p>
    </div>
  )
}

export function PaperPage() {
  const qc = useQueryClient()
  const listQ = useQuery({ queryKey: ["paperAccounts"], queryFn: fetchPaperAccounts })
  const [selected, setSelected] = useState<string | null>(null)
  const [account, setAccount] = useState<PaperAccount | null>(null)

  const items = listQ.data ?? []
  const active = selected ?? items.find((x) => x.enabled)?.strategyId ?? null

  useEffect(() => {
    if (!active) {
      setAccount(null)
      return
    }
    let alive = true
    fetchPaperAccount(active)
      .then((a) => alive && setAccount(a))
      .catch(() => alive && setAccount(null)) // 未开启过 → 空态
    return () => {
      alive = false
    }
  }, [active, items])

  async function toggle(strategyId: string, enabled: boolean) {
    try {
      await togglePaper(strategyId, enabled)
      qc.invalidateQueries({ queryKey: ["paperAccounts"] })
      toast.success(
        enabled ? "已开启模拟跟单：今日收盘后收信号，次日开盘首买" : "已暂停模拟跟单（历史保留）",
      )
      if (enabled) setSelected(strategyId)
    } catch {
      toast.error("操作失败，请重试")
    }
  }

  function confirmReset(strategyId: string, name: string) {
    toast(`重置「${name}」的模拟盘？净值、持仓与交易记录将全部清空`, {
      action: {
        label: "重置",
        onClick: () => {
          resetPaper(strategyId)
            .then(() => {
              qc.invalidateQueries({ queryKey: ["paperAccounts"] })
              setAccount(null)
              toast.success("已重置")
            })
            .catch(() => toast.error("重置失败"))
        },
      },
    })
  }

  const totalRet = account ? (account.equity - 1) * 100 : null

  return (
    <div className="mx-auto max-w-[1600px] px-4 py-6 lg:px-6">
      <div>
        <h1 className="text-lg font-semibold text-foreground">模拟盘</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          按策略自动跟单：盘后信号次日开盘买入，与回测同一套成交规则，检验策略的样本外真实表现
        </p>
      </div>

      <div className="mt-5 grid grid-cols-1 gap-5 lg:grid-cols-[300px_1fr]">
        {/* 策略开关列表 */}
        <div className="space-y-2">
          {items.length === 0 && (
            <p className="rounded-lg border border-border p-4 text-sm text-muted-foreground">
              还没有策略，先去策略工坊创建一个
            </p>
          )}
          {items.map((s) => (
            <div
              key={s.strategyId}
              className={cn(
                "flex cursor-pointer items-center gap-3 rounded-lg border p-3 transition-colors",
                active === s.strategyId
                  ? "border-primary/50 bg-accent/40"
                  : "border-border hover:bg-muted/40",
              )}
              onClick={() => setSelected(s.strategyId)}
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-foreground">{s.strategyName}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {s.equity != null ? (
                    <>
                      净值{" "}
                      <span
                        className={cn(
                          "font-mono tabular-nums",
                          s.equity >= 1 ? "text-up" : "text-down",
                        )}
                      >
                        {s.equity.toFixed(4)}
                      </span>
                      {s.lastSettled && ` · 结算至 ${s.lastSettled.slice(5)}`}
                    </>
                  ) : (
                    "未开启"
                  )}
                </p>
              </div>
              <Switch
                checked={s.enabled}
                onCheckedChange={(v: boolean) => toggle(s.strategyId, v)}
                onClick={(e: React.MouseEvent) => e.stopPropagation()}
                aria-label={`模拟跟单 ${s.strategyName}`}
              />
            </div>
          ))}
        </div>

        {/* 账户详情 */}
        <div className="min-w-0">
          {!account ? (
            <div className="flex h-64 flex-col items-center justify-center rounded-lg border border-dashed border-border text-center">
              <Robot className="size-8 text-muted-foreground/60" />
              <p className="mt-3 text-sm text-muted-foreground">
                {items.some((x) => x.enabled)
                  ? "选择左侧账户查看明细"
                  : "打开某个策略的开关，从下一个交易日开始自动模拟跟单"}
              </p>
            </div>
          ) : (
            <div className="space-y-5">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat
                  label="累计收益"
                  value={`${totalRet! >= 0 ? "+" : ""}${totalRet!.toFixed(2)}%`}
                  tone={totalRet! >= 0 ? "up" : "down"}
                />
                <Stat label="持仓 / 上限" value={`${account.positions.filter((p) => p.status === "open").length} / ${account.params.maxConcurrent}`} />
                <Stat label="已平仓交易" value={String(account.tradeCount)} />
                <Stat
                  label="平仓胜率"
                  value={account.winRate != null ? `${account.winRate}%` : "—"}
                />
              </div>

              {account.curve.length > 1 ? (
                <div className="rounded-lg border border-border p-4">
                  <BacktestChart
                    curve={account.curve}
                    benchmark={account.backtestOverlay?.curve ?? []}
                    benchmarkName="回测（同段归一）"
                  />
                  {account.backtestOverlay && (
                    <p className="mt-2 text-[11px] text-muted-foreground">
                      虚线为 {account.backtestOverlay.createdAt} 的最近一次回测在同一时段的归一曲线
                      ——两条线的分叉程度就是策略过拟合的直观读数。若回测参数与模拟盘不一致，
                      对比仅供参考。
                    </p>
                  )}
                </div>
              ) : (
                <p className="rounded-lg border border-border p-4 text-xs text-muted-foreground">
                  {account.startedAt
                    ? `已于 ${account.startedAt} 启动，净值曲线将随逐日结算展开`
                    : "等待首个盘后结算"}
                </p>
              )}

              {/* 持仓 */}
              <div className="rounded-lg border border-border">
                <p className="border-b border-border px-4 py-2.5 text-sm font-medium text-foreground">
                  当前持仓与排队
                </p>
                {account.positions.length === 0 ? (
                  <p className="px-4 py-3 text-xs text-muted-foreground">空仓</p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="border-b border-border text-muted-foreground">
                          <th className="px-4 py-2 text-left font-normal">股票</th>
                          <th className="px-2 py-2 text-left font-normal">状态</th>
                          <th className="px-2 py-2 text-right font-normal">入场价</th>
                          <th className="px-2 py-2 text-right font-normal">现价</th>
                          <th className="px-2 py-2 text-right font-normal">浮动收益</th>
                          <th className="px-4 py-2 text-right font-normal">持有日</th>
                        </tr>
                      </thead>
                      <tbody>
                        {account.positions.map((p) => (
                          <tr key={p.code} className="border-b border-border/60 last:border-0">
                            <td className="px-4 py-2">
                              {p.name} <span className="font-mono text-muted-foreground">{p.code}</span>
                            </td>
                            <td className="px-2 py-2">
                              {p.status === "open" ? "持仓" : "待入场（次日开盘）"}
                            </td>
                            <td className="px-2 py-2 text-right font-mono tabular-nums">
                              {p.status === "open" ? p.entryPx.toFixed(2) : "—"}
                            </td>
                            <td className="px-2 py-2 text-right font-mono tabular-nums">
                              {p.lastPrice.toFixed(2)}
                            </td>
                            <td
                              className={cn(
                                "px-2 py-2 text-right font-mono tabular-nums",
                                p.ret != null && (p.ret >= 0 ? "text-up" : "text-down"),
                              )}
                            >
                              {p.ret != null ? `${p.ret >= 0 ? "+" : ""}${p.ret}%` : "—"}
                            </td>
                            <td className="px-4 py-2 text-right font-mono tabular-nums">
                              {p.status === "open" ? p.holdDays : "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              {/* 交易流水 */}
              <div className="rounded-lg border border-border">
                <p className="border-b border-border px-4 py-2.5 text-sm font-medium text-foreground">
                  交易记录（近 100 笔）
                </p>
                {account.trades.length === 0 ? (
                  <p className="px-4 py-3 text-xs text-muted-foreground">还没有平仓交易</p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="border-b border-border text-muted-foreground">
                          <th className="px-4 py-2 text-left font-normal">股票</th>
                          <th className="px-2 py-2 text-left font-normal">入场</th>
                          <th className="px-2 py-2 text-left font-normal">出场</th>
                          <th className="px-2 py-2 text-right font-normal">收益</th>
                          <th className="px-4 py-2 text-left font-normal">原因</th>
                        </tr>
                      </thead>
                      <tbody>
                        {account.trades.map((t, i) => (
                          <tr key={`${t.code}-${t.exitDate}-${i}`} className="border-b border-border/60 last:border-0">
                            <td className="px-4 py-2">
                              {t.name} <span className="font-mono text-muted-foreground">{t.code}</span>
                            </td>
                            <td className="px-2 py-2 font-mono tabular-nums">
                              {t.entryDate.slice(5)} @{t.entryPx.toFixed(2)}
                            </td>
                            <td className="px-2 py-2 font-mono tabular-nums">
                              {t.exitDate.slice(5)} @{t.exitPx.toFixed(2)}
                            </td>
                            <td
                              className={cn(
                                "px-2 py-2 text-right font-mono tabular-nums",
                                t.ret >= 0 ? "text-up" : "text-down",
                              )}
                            >
                              {t.ret >= 0 ? "+" : ""}
                              {t.ret}%
                            </td>
                            <td className="px-4 py-2">{REASON_LABELS[t.reason] ?? t.reason}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              <div className="flex items-start justify-between gap-4">
                <p className="text-xs leading-relaxed text-muted-foreground">
                  成交规则与事件回测一致：盘后信号次日开盘买入，每笔占 1/
                  {account.params.maxConcurrent} 仓位；
                  {account.params.exitRule === "stop"
                    ? `止盈 ${account.params.stopGain}% / 止损 ${account.params.stopLoss}%（盘中触发价成交）`
                    : account.params.exitRule === "signal"
                      ? "反向信号日收盘卖出"
                      : `持有 ${account.params.holdDays} 个交易日后收盘卖出`}
                  ；一字涨停买不进顺延 3 日、一字跌停卖不出顺延 5 日
                  {(account.stats.skippedByLimit ?? 0) + (account.stats.skippedByCapacity ?? 0) > 0 &&
                    `。已放弃信号：涨停 ${account.stats.skippedByLimit ?? 0} 个、仓位满 ${account.stats.skippedByCapacity ?? 0} 个`}
                  。
                </p>
                <Button
                  size="sm"
                  variant="outline"
                  className="shrink-0 gap-1.5"
                  onClick={() => confirmReset(account.strategyId, account.strategyName)}
                >
                  <ArrowCounterClockwise className="size-3.5" />
                  重置
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
