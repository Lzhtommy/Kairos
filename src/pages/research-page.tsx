import { useMemo, useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { ArrowClockwise, MagnifyingGlass, Plus, Trash, Warning } from "@phosphor-icons/react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  deleteResearch,
  fetchResearchList,
  fetchResearchTasks,
  generateResearch,
  retryResearch,
  type GenerateResult,
  type Grade,
  type ResearchTask,
} from "@/api/research"
import { GradeBadge, MiniDims } from "@/components/research/research-ui"
import { fmtDate, fmtDateTime, isStale } from "@/lib/research-format"
import { useAuth } from "@/lib/auth"
import { cn } from "@/lib/utils"

const GRADES: Grade[] = ["A", "B", "C", "D"]

export function ResearchPage() {
  const { user } = useAuth()
  const isAdmin = !!user?.is_admin
  const [tab, setTab] = useState<"library" | "tasks">("library")
  const [dialogOpen, setDialogOpen] = useState(false)

  const tasksQ = useQuery({
    queryKey: ["research-tasks"],
    queryFn: fetchResearchTasks,
    enabled: isAdmin,
    refetchInterval: (q) =>
      q.state.data?.some((t) => t.status === "queued" || t.status === "running") ? 3000 : false,
  })
  const activeCount = tasksQ.data?.filter((t) => t.status === "queued" || t.status === "running").length ?? 0

  return (
    <div className="mx-auto max-w-6xl space-y-5 px-4 py-6 lg:px-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-foreground">研报</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            个股基本面评分与研究报告 · 程序基于公开数据生成，仅供研究参考，不构成投资建议
          </p>
        </div>
        {isAdmin && (
          <Button onClick={() => setDialogOpen(true)}>
            <Plus className="size-4" />
            生成研报
          </Button>
        )}
      </div>

      {isAdmin && (
        <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
          <TabsList>
            <TabsTrigger value="library">研报库</TabsTrigger>
            <TabsTrigger value="tasks">
              任务队列{activeCount > 0 && <span className="ml-1 tabular-nums text-primary">· {activeCount}</span>}
            </TabsTrigger>
          </TabsList>
        </Tabs>
      )}

      {tab === "library" || !isAdmin ? (
        <Library isAdmin={isAdmin} onGenerate={() => setDialogOpen(true)} />
      ) : (
        <TaskQueue tasks={tasksQ.data} loading={tasksQ.isLoading} />
      )}

      {isAdmin && (
        <GenerateDialog
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          onSubmitted={() => setTab("tasks")}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------- 研报库

function Library({ isAdmin, onGenerate }: { isAdmin: boolean; onGenerate: () => void }) {
  const navigate = useNavigate()
  const listQ = useQuery({ queryKey: ["research-list"], queryFn: fetchResearchList })
  const [q, setQ] = useState("")
  const [industry, setIndustry] = useState("all")
  const [grade, setGrade] = useState<Grade | null>(null)
  const [flaggedOnly, setFlaggedOnly] = useState(false)

  const items = useMemo(() => listQ.data ?? [], [listQ.data])
  const industries = useMemo(
    () => Array.from(new Set(items.map((i) => i.industry).filter((i) => i && i !== "—"))).sort(),
    [items],
  )
  const counts = useMemo(() => {
    const c: Record<string, number> = { A: 0, B: 0, C: 0, D: 0 }
    for (const i of items) if (i.grade) c[i.grade]++
    return c
  }, [items])

  const rows = items.filter(
    (i) =>
      (!q || i.code.includes(q) || i.name.includes(q)) &&
      (industry === "all" || i.industry === industry) &&
      (!grade || i.grade === grade) &&
      (!flaggedOnly || i.red_flags > 0),
  )

  if (listQ.isLoading) return <div className="py-10 text-sm text-muted-foreground">加载中…</div>

  if (items.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border py-16 text-center">
        <p className="text-sm text-muted-foreground">暂无研报</p>
        {isAdmin && (
          <Button className="mt-4" onClick={onGenerate}>
            <Plus className="size-4" />
            生成第一份研报
          </Button>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-5 gap-2">
        <div className="rounded-lg border border-border px-4 py-3">
          <div className="text-xs text-muted-foreground">覆盖</div>
          <div className="mt-1 text-xl font-semibold tabular-nums">{items.length}</div>
        </div>
        {GRADES.map((g) => (
          <button
            key={g}
            onClick={() => setGrade(grade === g ? null : g)}
            className={cn(
              "flex items-center justify-between rounded-lg border px-4 py-3 text-left transition-colors",
              grade === g ? "border-indigo-500 bg-indigo-500/5" : "border-border hover:bg-muted/50",
            )}
          >
            <GradeBadge grade={g} />
            <span className="text-xl font-semibold tabular-nums">{counts[g]}</span>
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative w-56">
          <MagnifyingGlass className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value.trim())} placeholder="代码 / 名称" className="pl-8" />
        </div>
        <Select value={industry} onValueChange={(v) => setIndustry(v ?? "all")}>
          <SelectTrigger className="w-40">
            <SelectValue>{industry === "all" ? "全部行业" : industry}</SelectValue>
          </SelectTrigger>
          <SelectContent alignItemWithTrigger={false} className="max-h-72">
            <SelectItem value="all">全部行业</SelectItem>
            {industries.map((ind) => (
              <SelectItem key={ind} value={ind}>
                {ind}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <Switch checked={flaggedOnly} onCheckedChange={setFlaggedOnly} />
          只看有排雷
        </label>
        <span className="ml-auto text-xs text-muted-foreground">按总分排序 · {rows.length} 只</span>
      </div>

      <section className="rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>股票</TableHead>
              <TableHead>行业</TableHead>
              <TableHead className="w-14 text-center">评级</TableHead>
              <TableHead className="w-16 text-right">总分</TableHead>
              <TableHead className="w-28">各维度</TableHead>
              <TableHead className="w-16 text-center">排雷</TableHead>
              <TableHead className="w-28">报告期</TableHead>
              <TableHead className="w-28">更新</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.length === 0 && (
              <TableRow>
                <TableCell colSpan={8} className="py-8 text-center text-muted-foreground">
                  没有符合条件的研报
                </TableCell>
              </TableRow>
            )}
            {rows.map((r) => (
              <TableRow key={r.id} className="cursor-pointer" onClick={() => navigate(`/app/research/${r.id}`)}>
                <TableCell>
                  <Link to={`/app/research/${r.id}`} className="block" onClick={(e) => e.stopPropagation()}>
                    <div className="font-medium text-foreground">{r.name}</div>
                    <div className="font-mono text-xs text-muted-foreground">{r.code}</div>
                  </Link>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">{r.industry}</TableCell>
                <TableCell className="text-center">
                  <GradeBadge grade={r.grade} />
                </TableCell>
                <TableCell className="text-right font-mono tabular-nums">
                  {r.total_score?.toFixed(1) ?? "—"}
                </TableCell>
                <TableCell>
                  <MiniDims dims={r.dims} />
                </TableCell>
                <TableCell className="text-center">
                  {r.red_flags > 0 ? (
                    <span className="inline-flex items-center gap-1 text-xs font-medium text-red-600 dark:text-red-400">
                      <Warning weight="fill" className="size-3.5" />
                      {r.red_flags}
                    </span>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell className="text-xs">
                  <span className={cn(isStale(r.data_as_of) && "text-muted-foreground/60")}>
                    {r.data_as_of ?? "—"}
                  </span>
                  {isStale(r.data_as_of) && <div className="text-[11px] text-muted-foreground">可能过期</div>}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {fmtDate(r.finished_at)}
                  {r.updating && <div className="text-[11px] text-primary">更新中…</div>}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </section>
    </div>
  )
}

// ---------------------------------------------------------------- 任务队列（管理员）

const STATUS: Record<ResearchTask["status"], { label: string; cls: string }> = {
  queued: { label: "排队", cls: "bg-muted text-muted-foreground" },
  running: { label: "运行中", cls: "bg-primary/10 text-primary" },
  done: { label: "完成", cls: "bg-indigo-500/10 text-indigo-700 dark:text-indigo-300" },
  failed: { label: "失败", cls: "bg-red-500/10 text-red-600 dark:text-red-400" },
}

function TaskQueue({ tasks, loading }: { tasks: ResearchTask[] | undefined; loading: boolean }) {
  const qc = useQueryClient()
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["research-tasks"] })
    qc.invalidateQueries({ queryKey: ["research-list"] })
  }
  const retry = useMutation({
    mutationFn: retryResearch,
    onSuccess: () => (toast.success("已重新排队"), refresh()),
    onError: (e) => toast.error(e instanceof Error ? e.message : "重试失败"),
  })
  const remove = useMutation({
    mutationFn: deleteResearch,
    onSuccess: () => (toast.success("已删除"), refresh()),
    onError: (e) => toast.error(e instanceof Error ? e.message : "删除失败"),
  })

  if (loading) return <div className="py-10 text-sm text-muted-foreground">加载中…</div>

  return (
    <section className="rounded-lg border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-20">状态</TableHead>
            <TableHead>股票</TableHead>
            <TableHead>进度 / 结果</TableHead>
            <TableHead className="w-36">提交时间</TableHead>
            <TableHead className="w-24 text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {(tasks ?? []).length === 0 && (
            <TableRow>
              <TableCell colSpan={5} className="py-8 text-center text-muted-foreground">
                暂无任务
              </TableCell>
            </TableRow>
          )}
          {(tasks ?? []).map((t) => (
            <TableRow key={t.id}>
              <TableCell>
                <Badge className={cn("border-transparent", STATUS[t.status].cls)}>{STATUS[t.status].label}</Badge>
              </TableCell>
              <TableCell>
                <span className="text-foreground">{t.name || t.code}</span>
                <span className="ml-2 font-mono text-xs text-muted-foreground">{t.code}</span>
              </TableCell>
              <TableCell className="max-w-md text-xs">
                {t.status === "done" ? (
                  <Link to={`/app/research/${t.id}`} className="inline-flex items-center gap-2 hover:underline">
                    <GradeBadge grade={t.grade} />
                    <span className="font-mono tabular-nums">{t.total_score?.toFixed(1) ?? "—"}</span>
                  </Link>
                ) : t.status === "failed" ? (
                  <span className="line-clamp-2 text-red-600 dark:text-red-400" title={t.error ?? ""}>
                    {t.error}
                  </span>
                ) : (
                  <span className="text-muted-foreground">{t.progress || "—"}</span>
                )}
              </TableCell>
              <TableCell className="text-xs text-muted-foreground">{fmtDateTime(t.created_at)}</TableCell>
              <TableCell className="text-right">
                <div className="flex justify-end gap-1">
                  {t.status === "failed" && (
                    <button
                      onClick={() => retry.mutate(t.id)}
                      className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                      aria-label="重试"
                      title="重试"
                    >
                      <ArrowClockwise className="size-3.5" />
                    </button>
                  )}
                  {t.status !== "running" && (
                    <button
                      onClick={() => remove.mutate(t.id)}
                      className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                      aria-label={t.status === "queued" ? "取消" : "删除"}
                      title={t.status === "queued" ? "取消" : "删除"}
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
  )
}

// ---------------------------------------------------------------- 生成对话框（管理员）

function GenerateDialog({
  open,
  onOpenChange,
  onSubmitted,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  onSubmitted: () => void
}) {
  const qc = useQueryClient()
  const [text, setText] = useState("")
  const [force, setForce] = useState(false)
  const [results, setResults] = useState<GenerateResult[] | null>(null)
  const codes = Array.from(new Set(text.match(/\d{6}/g) ?? []))

  const submit = useMutation({
    mutationFn: () => generateResearch(codes, force),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["research-tasks"] })
      const ok = res.filter((r) => r.ok).length
      const rejected = res.filter((r) => !r.ok)
      if (ok) toast.success(`已提交 ${ok} 只`)
      if (rejected.length) {
        setResults(rejected)
      } else {
        close()
      }
      if (ok) onSubmitted()
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "提交失败"),
  })

  function close() {
    onOpenChange(false)
    setText("")
    setResults(null)
    setForce(false)
  }

  return (
    <Dialog open={open} onOpenChange={(v) => (v ? onOpenChange(true) : close())}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>生成研报</DialogTitle>
          <DialogDescription>
            粘贴股票代码，逗号 / 空格 / 换行分隔，单次最多 50 只。任务串行执行，每只约 2~4 分钟。
          </DialogDescription>
        </DialogHeader>
        <Textarea
          value={text}
          onChange={(e) => {
            setText(e.target.value)
            setResults(null)
          }}
          placeholder={"600519\n300750, 000858"}
          rows={5}
          className="font-mono"
        />
        <div className="flex items-center justify-between text-sm">
          <label className="flex items-center gap-2 text-muted-foreground">
            <Switch checked={force} onCheckedChange={setForce} />
            强制重新生成（忽略今日已生成）
          </label>
          <span className="text-xs text-muted-foreground tabular-nums">识别到 {codes.length} 只</span>
        </div>
        {results && (
          <div className="max-h-40 space-y-1 overflow-y-auto rounded-md bg-muted/50 p-3 text-xs">
            <div className="mb-1 font-medium text-foreground">以下代码未受理：</div>
            {results.map((r) => (
              <div key={r.code} className="flex justify-between gap-3">
                <span className="font-mono">
                  {r.code} {r.name}
                </span>
                <span className="text-muted-foreground">{r.reason}</span>
              </div>
            ))}
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={close}>
            {results ? "关闭" : "取消"}
          </Button>
          <Button onClick={() => submit.mutate()} disabled={codes.length === 0 || submit.isPending}>
            {submit.isPending ? "提交中…" : "提交"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
