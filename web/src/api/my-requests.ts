import { apiClient } from './http'

export interface MyRequest {
  request_id: string
  request_type: string
  request_type_label: string
  title: string
  status: string
  status_label: string
  next_step: string
  needs_materials: string | null
  updated_at: string | null
  created_at: string | null
}

export const REQUEST_TYPES = [
  { value: 'policy_check', label: '制度核对', hint: '个人情形如何适用制度，需要人工确认' },
  { value: 'certificate', label: '证明开具', hint: '在职证明、收入证明等' },
  { value: 'process_help', label: '流程协助', hint: '流程操作遇到问题，需要协助' },
  { value: 'other', label: '其他事项', hint: '不属于以上类型的事项' },
] as const

export async function listMyRequests() {
  return apiClient.request<{ requests: MyRequest[] }>('/api/my-requests')
}

export function createMyRequest(body: { request_type: string; title: string; description: string }) {
  return apiClient.request<MyRequest>('/api/my-requests', { method: 'POST', body: JSON.stringify(body) })
}
