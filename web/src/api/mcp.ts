import { apiClient } from './http'

export interface McpToolView {
  name: string
  kind: 'read' | 'write'
  description: string | null
  input_schema: Record<string, unknown>
  requires_approval: boolean
  capability: string
}

export interface McpCapabilities {
  server: string
  scope: string
  transports: Array<{ kind: string; command?: string; url?: string; note: string }>
  tenant_id: string
  read_tools: McpToolView[]
  write_tools: McpToolView[]
  write_mode: string
  auth: string
}

export function getMcpCapabilities() {
  return apiClient.request<McpCapabilities>('/api/mcp/capabilities')
}

export function listMcpTools() {
  return apiClient.request<{ tools: McpToolView[]; count: number }>('/api/mcp/tools')
}

export function callMcpTool(toolName: string, args: Record<string, unknown>) {
  return apiClient.request<Record<string, unknown>>(`/api/mcp/tools/${encodeURIComponent(toolName)}/call`, {
    method: 'POST',
    body: JSON.stringify({ arguments: args }),
  })
}
