import { useSearchParams } from "react-router-dom"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ApiKeysPanel } from "@/components/admin/api-keys-panel"
import { InvitesPanel } from "@/components/admin/invites-panel"
import { UsersPanel } from "@/components/admin/users-panel"

type AdminTab = "users" | "invites" | "api-keys"

/** 用户管理（仅管理员）：用户、邀请码、数据 API 三个分栏，当前分栏记在 ?tab= 里，可直接分享链接 */
export function AdminUsersPage() {
  const [params, setParams] = useSearchParams()
  const raw = params.get("tab")
  const tab: AdminTab = raw === "invites" || raw === "api-keys" ? raw : "users"

  return (
    <div className="mx-auto max-w-4xl space-y-5 px-4 py-6 lg:px-6">
      <h1 className="text-lg font-semibold text-foreground">用户管理</h1>
      <Tabs
        value={tab}
        onValueChange={(v) => setParams(v === "users" ? {} : { tab: String(v) }, { replace: true })}
        className="gap-5"
      >
        <div className="border-b border-border">
          <TabsList variant="line">
            <TabsTrigger value="users" className="px-3">用户</TabsTrigger>
            <TabsTrigger value="invites" className="px-3">邀请码</TabsTrigger>
            <TabsTrigger value="api-keys" className="px-3">数据 API</TabsTrigger>
          </TabsList>
        </div>
        {/* 只挂载当前分栏：切换时重新拉取，删用户后邀请码的「使用者」不会停留在旧数据 */}
        <TabsContent value="users">
          <UsersPanel />
        </TabsContent>
        <TabsContent value="invites">
          <InvitesPanel />
        </TabsContent>
        <TabsContent value="api-keys">
          <ApiKeysPanel />
        </TabsContent>
      </Tabs>
    </div>
  )
}
