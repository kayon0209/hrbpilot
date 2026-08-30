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
