import { apiClient } from './http'

/** 任务型网关工具调用的终态（后端 app/mcp/contract.py 扩展后的 ToolOutcome）。 */
export type AgentTaskOutcome =
  | 'FOUND'
  | 'NO_EVIDENCE'
  | 'INPUT_REQUIRED'
  | 'AWAITING_CONFIRMATION'
  | 'AWAITING_APPROVAL'
  | 'RUNNING'
  | 'SUCCEEDED'
  | 'CANCELLED'
  | 'EXPIRED'
  | 'AUTH_REQUIRED'
  | 'FORBIDDEN'
  | 'FAILED'

export interface MissingField {
  field: string
  label: string
  question: string
}

export interface PlannedStep {
  label: string
  effect: string
}

export interface AgentTaskInfo {
  task_id: string
  task_type: string
  title: string
  status: string
  risk_level: string
  source_surface: string
  updated_at: string | null
  next: string
  user_view?: { web_path?: string }
  draft_version?: number
  approval_id?: string | null
  case_id?: string | null
  work_task_id?: string | null
  missing_fields?: MissingField[]
  planned_steps?: PlannedStep[]
  last_error_code?: string | null
  expires_at?: string | null
}

export interface PrepareResult {
  ok: boolean
  tool: string
  outcome?: AgentTaskOutcome
  user_message?: string
  error_code?: string
  fix?: string | null
  retryable?: boolean
  detail?: string
  task_id?: string
  draft_version?: number
  status?: string
  task_type?: string
  interpreted_goal?: string
  missing_fields?: MissingField[]
  planned_steps?: PlannedStep[]
  confirmation_summary?: string
  expires_at?: string | null
  [key: string]: unknown
}

export interface AgentTaskListResult {
  ok: boolean
  items: AgentTaskInfo[]
  total: number
  truncated: boolean
  next_cursor: string | null
  user_message?: string
  [key: string]: unknown
}

export const prepareAgentTask = (goal: string, caseRef?: string) =>
  apiClient.request<PrepareResult>('/api/agent-tasks/prepare', {
    method: 'POST',
    body: JSON.stringify({ goal, case_ref: caseRef || undefined }),
  })

export const provideAgentTaskInput = (taskId: string, fields: Record<string, string>) =>
  apiClient.request<PrepareResult>(`/api/agent-tasks/${encodeURIComponent(taskId)}/input`, {
    method: 'POST',
    body: JSON.stringify({ fields }),
  })

export const submitAgentTask = (taskId: string, draftVersion: number, idempotencyKey: string) =>
  apiClient.request<PrepareResult>(`/api/agent-tasks/${encodeURIComponent(taskId)}/submit`, {
    method: 'POST',
    body: JSON.stringify({ draft_version: draftVersion, idempotency_key: idempotencyKey }),
  })

export const getAgentTaskStatus = (taskId: string) =>
  apiClient.request<PrepareResult & AgentTaskInfo>(`/api/agent-tasks/${encodeURIComponent(taskId)}`)

export const listAgentTasks = (params?: { status?: string; limit?: number; offset?: number; response_format?: string }) => {
  const query = new URLSearchParams()
  if (params?.status) query.set('status', params.status)
  if (params?.limit !== undefined) query.set('limit', String(params.limit))
  if (params?.offset !== undefined) query.set('offset', String(params.offset))
  if (params?.response_format) query.set('response_format', params.response_format)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return apiClient.request<AgentTaskListResult>(`/api/agent-tasks${suffix}`)
}

export const cancelAgentTask = (taskId: string) =>
  apiClient.request<PrepareResult>(`/api/agent-tasks/${encodeURIComponent(taskId)}/cancel`, {
    method: 'POST',
  })

export interface AgentTaskEvent {
  seq: number
  event_type: string
  from_status: string | null
  to_status: string | null
  at: string | null
  payload: Record<string, unknown>
}

export const getAgentTaskEvents = (taskId: string) =>
  apiClient.request<{ task_id: string; events: AgentTaskEvent[] }>(
    `/api/agent-tasks/${encodeURIComponent(taskId)}/events`,
  )

/* ------------------------------- 审批队列 ------------------------------- */

/** 审批队列条目。后端 ``GET /api/agent-tasks/approvals``（hr_manager only）。
 *  决定动作本身走既有业务接口 ``POST /api/v1/hr-cases/{case_id}/approve``，
 *  队列只负责把 ``case_id`` / ``approval_id`` 两条 ID 送到决定人眼前。 */
export interface AgentApprovalItem {
  task_id: string
  title: string
  task_type: string
  risk_level: string
  requester_name: string
  approval_id: string
  case_id: string
  updated_at: string | null
  web_path: string
}

export interface AgentApprovalQueueResult {
  ok: boolean
  items: AgentApprovalItem[]
  user_message?: string
  [key: string]: unknown
}

export const listApprovalQueue = () =>
  apiClient.request<AgentApprovalQueueResult>('/api/agent-tasks/approvals')

/** 批准 / 驳回一条 AI 发起的办理。决定人是 hr_manager —— 角色校验在后端
 *  ``decide_approval``（``DECIDER_ROLES``），前端不重复设防也不假装有自批限制。 */
export const decideApproval = (caseId: string, approvalId: string, decision: 'approve' | 'reject', reason?: string) =>
  apiClient.request<{ approval_id: string; status: string }>(
    `/api/v1/hr-cases/${encodeURIComponent(caseId)}/approve`,
    {
      method: 'POST',
      body: JSON.stringify({ approval_id: approvalId, decision, reason: reason || undefined }),
    },
  )

/** 批准之后的第二步（后端刻意拆成的两次调用：先决定、再执行，各自留审计）。
 *  ``request_id`` 是这次执行的幂等键。没有后台 worker 会替你跑这一步 ——
 *  审批队列里「批准并执行」就是靠它收尾。 */
export const executeApprovedCase = (caseId: string, approvalId: string, requestId: string) =>
  apiClient.request<{ approval_id: string; execution_id: string; grant_id: string }>(
    `/api/v1/hr-cases/${encodeURIComponent(caseId)}/execute`,
    {
      method: 'POST',
      body: JSON.stringify({ approval_id: approvalId, request_id: requestId }),
    },
  )
