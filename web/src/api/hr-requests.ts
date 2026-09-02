import { apiClient } from './http'

export interface HrDeliveryAttempt {
  attempt_id: string
  status: 'queued' | 'simulated_accepted' | 'retryable_failed' | 'rejected'
  attempt_count: number
  provider_msgid: string | null
  safe_message: string
  retryable: boolean
  error: string | null
}

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
  connector_source_label: string | null
  delivery: HrDeliveryAttempt | null
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

export function retryHrDelivery(requestId: string, attemptId: string) {
  return apiClient.request<HrDeliveryAttempt>(
    `/api/hr-requests/${requestId}/delivery-attempts/${attemptId}/retry`,
    { method: 'POST' },
  )
}
