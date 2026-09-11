import { apiClient } from './http'

export interface AuditEvent {
  event_id: string
  action: string
  actor_id: string
  actor_name?: string | null
  actor_email?: string | null
  object_type: string | null
  object_id: string | null
  /** Present for legacy RAG rows whose payload is plain text, not JSON. */
  input_summary?: string | null
  details: Record<string, unknown>
  created_at: string
}

export const listAuditEvents = () => apiClient.request<{ events: AuditEvent[] }>('/api/audit/events?limit=100')
