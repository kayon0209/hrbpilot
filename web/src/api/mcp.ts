import { apiClient } from './http'

/** 工具调用的终态。与后端 app/mcp/contract.py 的 ToolOutcome 一一对应。 */
export type ToolOutcome = 'FOUND' | 'NO_EVIDENCE' | 'AWAITING_APPROVAL' | 'AUTH_REQUIRED' | 'FORBIDDEN' | 'FAILED'

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
