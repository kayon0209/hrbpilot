import { apiClient } from './http'

export interface HrRequestItem {
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
  description: string
  hr_note: string | null
  hr_case_id: string | null
  hr_owner_id: string | null
}

export interface Assignee {
  user_id: string
  name: string
  email: string
}

export async function listHrRequests() {
  return apiClient.request<{ requests: HrRequestItem[] }>('/api/hr-requests')
}

export async function listAssignees() {
  return apiClient.request<{ assignees: Assignee[] }>('/api/hr-requests/assignees')
}

export function triageHrRequest(requestId: string, body: {
  status: 'needs_materials' | 'in_progress' | 'resolved'
  next_step_for_employee?: string
  needs_materials?: string
  hr_note?: string
  hr_owner_id?: string
}) {
  return apiClient.request(`/api/hr-requests/${requestId}/triage`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}
