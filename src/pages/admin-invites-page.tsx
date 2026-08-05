import { useEffect, useState } from "react"
import { toast } from "sonner"
import { Copy, Plus, Ticket, Trash } from "@phosphor-icons/react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { fetchInvites, createInvites, deleteInvite, type Invite } from "@/api/admin"

function fmtTime(iso: string | null): string {
  if (!iso) return "—"
  const d = new Date(iso)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate(),
  ).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`
}

export function AdminInvitesPage() {
  const [invites, setInvites] = useState<Invite[] | null>(null)
  const [count, setCount] = useState(5)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    fetchInvites().then(setInvites).catch(() => toast.error("邀请码加载失败"))
  }, [])

  async function generate() {
    setBusy(true)
    try {
      const created = await createInvites(count)
      setInvites((prev) => [...created, ...(prev ?? [])])
      toast.success(`已生成 ${created.length} 个邀请码`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "生成失败")
    } finally {
      setBusy(false)
    }
  }

  async function remove(id: number) {
    try {
      await deleteInvite(id)
      setInvites((prev) => prev?.filter((i) => i.id !== id) ?? null)
      toast.success("已删除")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败")
    }
  }

  function copy(text: string) {
    navigator.clipboard.writeText(text).then(
      () => toast.success("已复制"),
      () => toast.error("复制失败"),
    )
  }

  const unused = invites?.filter((i) => !i.usedBy) ?? []

  if (!invites) {
    return <div className="p-6 text-sm text-muted-foreground">加载中…</div>
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6 px-4 py-6 lg:px-6">
      <div>
        <h1 className="text-lg font-semibold text-foreground">邀请码管理</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          注册需要邀请码，一码一用 · 未使用 {unused.length} / 共 {invites.length}
        </p>
      </div>

      <section className="space-y-4 rounded-lg border border-border p-5">
        <div className="flex items-center gap-2">
          <Ticket className="size-4 text-primary" />
          <h2 className="text-sm font-medium text-foreground">生成新邀请码</h2>
        </div>
        <div className="flex items-center gap-3">
          <Input
            type="number"
            min={1}
            max={100}
            value={count}
            onChange={(e) => setCount(Math.max(1, Math.min(100, Number(e.target.value) || 1)))}
            className="w-24"
          />
          <Button onClick={generate} disabled={busy}>
            <Plus className="size-4" />
            {busy ? "生成中…" : "生成"}
          </Button>
          {unused.length > 0 && (
            <Button
              variant="outline"
              onClick={() => copy(unused.map((i) => i.code).join("\n"))}
            >
              <Copy className="size-4" />
              复制全部未使用
            </Button>
          )}
        </div>
      </section>

      <section className="rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>邀请码</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>使用者</TableHead>
              <TableHead>使用时间</TableHead>
              <TableHead className="w-20 text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {invites.length === 0 && (
              <TableRow>
                <TableCell colSpan={5} className="py-8 text-center text-muted-foreground">
                  还没有邀请码，点上方"生成"创建
                </TableCell>
              </TableRow>
            )}
            {invites.map((i) => (
              <TableRow key={i.id}>
                <TableCell className="font-mono text-xs">{i.code}</TableCell>
                <TableCell>
                  {i.usedBy ? (
                    <Badge variant="secondary">已使用</Badge>
                  ) : (
                    <Badge className="bg-primary/10 text-primary hover:bg-primary/10">
                      未使用
                    </Badge>
                  )}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {i.usedBy ?? "—"}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {fmtTime(i.usedAt)}
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-1">
                    <button
                      onClick={() => copy(i.code)}
                      className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                      aria-label="复制邀请码"
                    >
                      <Copy className="size-3.5" />
                    </button>
                    {!i.usedBy && (
                      <button
                        onClick={() => remove(i.id)}
                        className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                        aria-label="删除邀请码"
                      >
                        <Trash className="size-3.5" />
                      </button>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </section>
    </div>
  )
}
