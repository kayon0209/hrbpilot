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

export interface Readiness {
  status: string
  checks?: Record<string, boolean | string | { status?: string; detail?: string }>
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
