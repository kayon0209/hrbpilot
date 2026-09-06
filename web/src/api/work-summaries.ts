import { apiClient } from './http'

export interface WorkSummary {
  work_id: string
  work_type: string
  title: string
  business_status: string
  next_action: string
  resume_target: string
  updated_at: string | null
  due_at: string | null
  owner: string | null
  waiting_for: string | null
  progress_mode: 'none' | 'stage' | 'units'
  completed_units: number | null
  total_units: number | null
}

export interface WorkSummaries {
  continue_work: WorkSummary | null
  attention: WorkSummary[]
  completed_today: WorkSummary[]
}

export const getWorkSummaries = () => apiClient.request<WorkSummaries>('/api/work-summaries')

export interface CreateWorkTaskInput {
  title: string
  next_action: string
  owner_user_id?: string
  waiting_for?: string | null
  due_at?: string | null
  total_units?: number | null
  idempotency_key?: string
}

export const createWorkTask = (input: CreateWorkTaskInput) =>
  apiClient.request('/api/work-summaries/tasks', { method: 'POST', body: JSON.stringify(input) })

export const createWorkSubtask = (taskId: string, input: CreateWorkTaskInput) =>
  apiClient.request(`/api/work-summaries/tasks/${taskId}/subtasks`, {
    method: 'POST',
    body: JSON.stringify(input),
  })

export interface AssignableOwner {
  user_id: string
  name: string
}

export const getAssignableOwners = () =>
  apiClient.request<{ owners: AssignableOwner[] }>('/api/work-summaries/assignable-owners')

export interface UpdateWorkTaskInput {
  title?: string
  next_action?: string
  owner_user_id?: string
  waiting_for?: string | null
  due_at?: string | null
  status?: string
  completed_units?: number
  total_units?: number
}

export const updateWorkTask = (
  taskId: string,
  input: UpdateWorkTaskInput,
) => apiClient.request(`/api/work-summaries/tasks/${taskId}`, {
  method: 'PATCH',
  body: JSON.stringify(input),
})

export const advanceWorkTask = (taskId: string) =>
  apiClient.request(`/api/work-summaries/tasks/${taskId}/advance`, {
    method: 'POST',
  })
