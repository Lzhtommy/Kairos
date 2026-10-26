import { useEffect, useState } from "react"
import { toast } from "sonner"
import { Copy, Key, Trash } from "@phosphor-icons/react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  fetchUsers,
  setUserAdmin,
  resetUserPassword,
  deleteUser,
  type AdminUser,
} from "@/api/admin"
import { useAuth } from "@/lib/auth"

function fmtDate(iso: string | null): string {
  if (!iso) return "—"
  const d = new Date(iso)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`
}

type Pending =
  | { kind: "delete"; user: AdminUser }
  | { kind: "reset"; user: AdminUser; password?: string }

export function AdminUsersPage() {
  const { user: me } = useAuth()
  const [users, setUsers] = useState<AdminUser[] | null>(null)
  const [pending, setPending] = useState<Pending | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    fetchUsers().then(setUsers).catch(() => toast.error("用户列表加载失败"))
  }, [])

  async function toggleAdmin(u: AdminUser, isAdmin: boolean) {
    try {
      const updated = await setUserAdmin(u.id, isAdmin)
      setUsers((prev) => prev?.map((x) => (x.id === u.id ? updated : x)) ?? null)
      toast.success(isAdmin ? `已将 ${u.username} 设为管理员` : `已撤销 ${u.username} 的管理员`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "操作失败")
    }
  }

  async function confirm() {
    if (!pending) return
    setBusy(true)
    try {
      if (pending.kind === "delete") {
        await deleteUser(pending.user.id)
        setUsers((prev) => prev?.filter((x) => x.id !== pending.user.id) ?? null)
        toast.success(`已删除 ${pending.user.username}`)
        setPending(null)
      } else {
        const password = await resetUserPassword(pending.user.id)
        setPending({ ...pending, password })
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "操作失败")
    } finally {
      setBusy(false)
    }
  }

  function copy(text: string) {
    navigator.clipboard.writeText(text).then(
      () => toast.success("已复制"),
      () => toast.error("复制失败"),
    )
  }

  if (!users) {
    return <div className="p-6 text-sm text-muted-foreground">加载中…</div>
  }

  const adminCount = users.filter((u) => u.isAdmin).length

  return (
    <div className="mx-auto max-w-4xl space-y-6 px-4 py-6 lg:px-6">
      <div>
        <h1 className="text-lg font-semibold text-foreground">用户管理</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          共 {users.length} 位用户 · 管理员 {adminCount} 位
        </p>
      </div>

      <section className="rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>用户</TableHead>
              <TableHead>策略数</TableHead>
              <TableHead>注册时间</TableHead>
              <TableHead>管理员</TableHead>
              <TableHead className="w-24 text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {users.map((u) => {
              const isMe = u.id === me?.id
              return (
                <TableRow key={u.id}>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-foreground">{u.username}</span>
                      {isMe && <Badge variant="secondary">你</Badge>}
                    </div>
                  </TableCell>
                  <TableCell className="font-mono text-xs tabular-nums text-muted-foreground">
                    {u.strategyCount}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">{fmtDate(u.createdAt)}</TableCell>
                  <TableCell>
                    <Switch
                      checked={u.isAdmin}
                      disabled={isMe}
                      onCheckedChange={(v) => toggleAdmin(u, v)}
                      aria-label={`${u.username} 管理员权限`}
                    />
                  </TableCell>
                  <TableCell className="text-right">
                    {!isMe && (
                      <div className="flex justify-end gap-1">
                        <button
                          onClick={() => setPending({ kind: "reset", user: u })}
                          className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                          aria-label="重置密码"
                          title="重置密码"
                        >
                          <Key className="size-3.5" />
                        </button>
                        <button
                          onClick={() => setPending({ kind: "delete", user: u })}
                          className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                          aria-label="删除用户"
                          title="删除用户"
                        >
                          <Trash className="size-3.5" />
                        </button>
                      </div>
                    )}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </section>

      <Dialog open={pending !== null} onOpenChange={(v) => !v && !busy && setPending(null)}>
        <DialogContent className="sm:max-w-md">
          {pending?.kind === "delete" && (
            <>
              <DialogHeader>
                <DialogTitle>删除用户</DialogTitle>
                <DialogDescription>
                  将永久删除 {pending.user.username} 及其{" "}
                  {pending.user.strategyCount} 个策略、回测与模拟盘数据，无法恢复。
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button variant="outline" onClick={() => setPending(null)} disabled={busy}>
                  取消
                </Button>
                <Button variant="destructive" onClick={confirm} disabled={busy}>
                  {busy ? "删除中…" : "确认删除"}
                </Button>
              </DialogFooter>
            </>
          )}
          {pending?.kind === "reset" && (
            <>
              <DialogHeader>
                <DialogTitle>重置密码</DialogTitle>
                <DialogDescription>
                  {pending.password
                    ? "新密码只显示这一次，请复制后发给用户。"
                    : `将为 ${pending.user.username} 生成一个随机新密码，原密码立即失效。`}
                </DialogDescription>
              </DialogHeader>
              {pending.password && (
                <div className="flex items-center justify-between gap-2 rounded-md border border-border bg-muted/50 px-3 py-2">
                  <span className="font-mono text-sm text-foreground">{pending.password}</span>
                  <button
                    onClick={() => copy(pending.password!)}
                    className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                    aria-label="复制新密码"
                  >
                    <Copy className="size-3.5" />
                  </button>
                </div>
              )}
              <DialogFooter>
                {pending.password ? (
                  <Button onClick={() => setPending(null)}>完成</Button>
                ) : (
                  <>
                    <Button variant="outline" onClick={() => setPending(null)} disabled={busy}>
                      取消
                    </Button>
                    <Button onClick={confirm} disabled={busy}>
                      {busy ? "重置中…" : "确认重置"}
                    </Button>
                  </>
                )}
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}
