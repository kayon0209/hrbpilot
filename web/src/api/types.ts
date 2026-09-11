export type UserRole = 'hrbp' | 'hr_manager' | 'admin' | string

export interface UserProfile {
  id: string
  email: string
  name: string
  role: UserRole
  tenant_id: string
}

export interface TokenPair {
  access_token: string
  refresh_token: string
  expires_in: number
}

export interface ApiErrorBody {
  code?: string
  message?: string
  detail?: string | { message?: string }
  request_id?: string
}

export type DependencyTier = 'critical' | 'optional'
export type DependencyStatus = 'ok' | 'unavailable' | 'error'

export interface ReadinessCheck {
  status: DependencyStatus
  tier: DependencyTier
}

export interface Readiness {
  status: 'ok' | 'degraded' | 'not_ready'
  checks?: Record<string, ReadinessCheck>
  critical_failed?: string[]
  optional_unavailable?: string[]
  request_id?: string
}

export interface KnowledgeBaseSummary {
  id: string
  name: string
  description?: string
  document_count?: number
  created_at?: string
}

export type UnknownRecord = Record<string, unknown>
