import { apiClient } from './http'

/** 工具调用的终态。与后端 app/mcp/contract.py 的 ToolOutcome 一一对应。 */
export type ToolOutcome = 'FOUND' | 'NO_EVIDENCE' | 'AWAITING_APPROVAL' | 'AWAITING_CONFIRMATION' | 'INPUT_REQUIRED' | 'RUNNING' | 'SUCCEEDED' | 'CANCELLED' | 'EXPIRED' | 'AUTH_REQUIRED' | 'FORBIDDEN' | 'FAILED'

export interface McpToolView {
  name: string
  kind: 'read' | 'write'
  description: string | null
  /** 参数结构。界面据它生成中文业务表单；不再原样铺给用户看。 */
  input_schema: Record<string, unknown>
  requires_approval: boolean
  capability: string
}

export interface McpCapabilities {
  server: string
  scope: string
  role: string
  transports: Array<{ kind: string; command?: string; url?: string; note: string }>
  tenant_id: string
  read_tools: McpToolView[]
  write_tools: McpToolView[]
  /** 因当前角色权限被收窄掉的能力数量（不返回名称）。 */
  hidden_tool_count: number
  hidden_reason: string | null
  write_mode: string
  auth: string
  authorization?: string
  task_gateway?: {
    default_bundle: string
    entrypoint: string
    tools: string[]
    task_types?: string[]
    manifest_version?: string
    confirmation: string
  }
  /**
   * 两档授权套餐（仅查询 / 查询 + 发起办理申请），由后端从 capability manifest
   * 派生。前端据此拼接入命令 —— 不在这里硬编码 scope 串，那会是第二份事实源，
   * 且改了后端没改前端会让用户照着配出一个权限不对的助手。
   */
  scope_packages?: Record<string, { label?: string; description?: string; scopes?: string[] }>
  /**
   * 「我接过的助手」——只含当前登录用户自己的活跃安装实例。
   *
   * 注意：字段**缺席**与**空数组**是两件事。缺席表示这次没查到（或后端降级），
   * 空数组表示确实一个都没接。界面必须能区分，否则会把"读不到"说成"你没有"。
   */
  my_installations?: McpInstallation[]
}

export interface McpToolResult {
  ok: boolean
  tool: string
  outcome?: ToolOutcome
  user_message?: string
  error_code?: string
  approval_id?: string
  status?: string
  summary?: string
  chunks?: unknown[]
  document?: { filename?: string } | null
  [key: string]: unknown
}

export interface McpInstallation {
  family_id: string
  client_id: string
  user_id: string
  user_name: string | null
  role: string
  active_refresh_tokens: number
  created_at: string | null
  last_rotated_at: string | null
  revoked_at: string | null
  resource?: string
  /** 该实例声明的权限范围（后端按轮换链聚合去重）。界面据此显示「仅查询 / 可提交办理」。 */
  scopes?: string[]
}

export interface McpClientConnection {
  client_id: string
  client_name: string
  registration_source: string
  installations: number
  active_installations: number
  last_seen_at: string | null
  blocked: boolean
}

export function getMcpCapabilities() {
  return apiClient.request<McpCapabilities>('/api/mcp/capabilities')
}

/* ---- 「我的连接」（连接管理中心）----
 * 与 `/capabilities` 分开：那边是静态能力清单，这边是动态运行状态（最近调用、失败、
 * 近七天用量，来自调用审计表）。字段语义见 `app/mcp/connection_status.py`。 */

export interface ConnectionCall {
  tool: string
  outcome: string
  latency_ms: number | null
  at: string
}

export interface MyConnectionInstall extends McpInstallation {
  status: 'active' | 'revoked'
  last_call: ConnectionCall | null
  /** 已被后续成功覆盖的失败不会出现在这里（后端按时间先后判定"是否已被覆盖"）。 */
  last_failure: (ConnectionCall & { deny_reason: string | null }) | null
  last_call_ok_after_failure: boolean
  calls_7d: number
}

export type ConnectionCheckCode = 'no_installations' | 'all_inactive' | 'no_calls_yet' | 'recent_failure' | 'healthy'

export interface MyConnections {
  checked_at: string
  installations: MyConnectionInstall[]
  server: { tool_count: number; hidden_tool_count: number }
  /** **结构化结论**：事实由后端给，措辞由前端出 —— 助手名的映射只有前端有。 */
  check: {
    code: ConnectionCheckCode
    installs: number
    live: number
    failing: number
    tool_count: number
    latest_call: ConnectionCall | null
  }
}

export function getMyConnections() {
  return apiClient.request<MyConnections>('/api/mcp/my-connections')
}

/**
 * 解绑一个**自己接过**的助手。
 *
 * 路径是 `/api/mcp/...`（用户面）而不是 `/api/admin/mcp/...`：后者需要管理员能力，
 * 普通 HR 调不动 —— 而"自己接的能自己解绑"正是这个功能存在的理由。撤销不属于自己的
 * 实例时后端返回 404（与"不存在"同一个结果，不泄漏某个 id 是否存在）。
 */
export function revokeMyInstallation(familyId: string) {
  return apiClient.request<{ family_id: string; revoked: boolean; user_message: string }>(
    `/api/mcp/my-installations/${encodeURIComponent(familyId)}/revoke`,
    { method: 'POST' },
  )
}

export function listMcpTools() {
  return apiClient.request<{ tools: McpToolView[]; count: number }>('/api/mcp/tools')
}

export function callMcpTool(toolName: string, args: Record<string, unknown>) {
  return apiClient.request<McpToolResult>(`/api/mcp/tools/${encodeURIComponent(toolName)}/call`, {
    method: 'POST',
    body: JSON.stringify({ arguments: args }),
  })
}

export const listMcpInstallations = () => apiClient.request<McpInstallation[]>('/api/admin/mcp/installations')
export const listMcpClients = () => apiClient.request<McpClientConnection[]>('/api/admin/mcp/clients')
export const revokeMcpInstallation = (familyId: string) =>
  apiClient.request(`/api/admin/mcp/installations/${encodeURIComponent(familyId)}/revoke`, { method: 'POST' })
export const revokeMcpClient = (clientId: string) =>
  apiClient.request(`/api/admin/mcp/clients/${encodeURIComponent(clientId)}/revoke`, { method: 'POST' })
export const enableMcpClient = (clientId: string) =>
  apiClient.request(`/api/admin/mcp/clients/${encodeURIComponent(clientId)}/enable`, { method: 'POST' })
export const revokeAllMcpInstallations = () =>
  apiClient.request('/api/admin/mcp/revoke-all', { method: 'POST' })
