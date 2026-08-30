import { apiClient } from './http'

export interface FeedbackCandidate {
  candidate_id: string
  source_type: 'no_evidence' | 'negative_feedback' | 'repeated_theme'
  source_label: string
  question: string
  occurrences: number
  evidence_summary: string | null
  suggested_kb_id: string | null
  status: 'open' | 'confirmed' | 'rejected' | 'assigned'
  handled_by: string | null
  handled_reason: string | null
  assignee: string | null
  updated_at: string | null
}

export async function listCandidates() {
  const result = await apiClient.request<{ candidates: FeedbackCandidate[] }>('/api/knowledge-feedback/candidates')
  return result
}

export function decideCandidate(body: { candidate_id: string; decision: 'confirm' | 'assign' | 'reject'; reason?: string; assignee?: string }) {
  return apiClient.request<FeedbackCandidate>('/api/knowledge-feedback/candidates/decide', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}
