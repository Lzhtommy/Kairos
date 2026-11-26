import { useEffect, useState } from "react"
import { toast } from "sonner"
import { PencilSimple, Trash } from "@phosphor-icons/react"
import { Badge } from "@/components/ui/badge"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { TablePager, usePagedRows } from "@/components/ui/table-pager"
import { adminRevokeApiKey, fetchAllApiKeys, setApiKeyLimit, type AdminApiKeyItem } from "@/api/api-keys"

function fmt(iso: string | null) {
  return iso ? new Date(iso).toLocaleString("zh-CN", { hour12: false }).slice(0, 16) : "—"
}

/** 用户管理页的「数据 API」分栏：全部 Key、今日用量、单独调整每日上限、吊销 */
export function ApiKeysPanel() {
  const [keys, setKeys] = useState<AdminApiKeyItem[] | null>(null)
  const load = () => fetchAllApiKeys().then(setKeys).catch(() => toast.error("API Key 加载失败"))
  useEffect(() => {
    load()
  }, [])
  const { pageRows, pager } = usePagedRows(keys ?? [], "all")

  async function editLimit(k: AdminApiKeyItem) {
    const v = window.prompt(`「${k.username} / ${k.name}」每日调用上限`, String(k.dailyLimit))
    if (v === null) return
    const n = Number(v)
    if (!Number.isInteger(n) || n < 0) {
      toast.error("请输入非负整数")
      return
    }
    try {
      await setApiKeyLimit(k.id, n)
      toast.success("已调整")
      load()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "调整失败")
    }
  }

  async function revoke(k: AdminApiKeyItem) {
    if (!window.confirm(`吊销「${k.username} / ${k.name}」？对方的 agent 会立即无法调用。`)) return
    try {
      await adminRevokeApiKey(k.id)
      toast.success("已吊销")
      load()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "吊销失败")
    }
  }

  if (!keys) return <div className="py-6 text-sm text-muted-foreground">加载中…</div>
  const active = keys.filter((k) => !k.revokedAt)

  return (
    <div className="space-y-5">
      <p className="text-sm text-muted-foreground">
        用户在设置页自助创建，用于 MCP 数据服务（/api/mcp） · 有效 {active.length} / 共 {keys.length} · 今日共{" "}
        {active.reduce((s, k) => s + k.usedToday, 0)} 次调用
      </p>
      <section className="rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>用户</TableHead>
              <TableHead>名称</TableHead>
              <TableHead>今日 / 上限</TableHead>
              <TableHead>最近使用</TableHead>
              <TableHead>状态</TableHead>
              <TableHead className="w-20 text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {keys.length === 0 && (
              <TableRow>
                <TableCell colSpan={6} className="py-8 text-center text-muted-foreground">
                  还没有用户创建 API Key
                </TableCell>
              </TableRow>
            )}
            {pageRows.map((k) => (
              <TableRow key={k.id}>
                <TableCell className="text-sm">{k.username}</TableCell>
                <TableCell className="text-xs">
                  {k.name} <code className="font-mono text-muted-foreground">{k.prefix}…</code>
                </TableCell>
                <TableCell className="font-mono text-xs tabular-nums">
                  {k.usedToday} / {k.dailyLimit}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">{fmt(k.lastUsedAt)}</TableCell>
                <TableCell>
                  {k.revokedAt ? <Badge variant="secondary">已吊销</Badge> : <Badge>有效</Badge>}
                </TableCell>
                <TableCell className="text-right">
                  {!k.revokedAt && (
                    <div className="flex justify-end gap-1">
                      <button
                        onClick={() => editLimit(k)}
                        className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                        aria-label="调整每日上限"
                      >
                        <PencilSimple className="size-3.5" />
                      </button>
                      <button
                        onClick={() => revoke(k)}
                        className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                        aria-label="吊销"
                      >
                        <Trash className="size-3.5" />
                      </button>
                    </div>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <TablePager {...pager} />
      </section>
    </div>
  )
}
