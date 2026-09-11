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
  /** ``ok`` reachable · ``unavailable`` optional dep down · ``error`` critical dep down */
  status: DependencyStatus
  tier: DependencyTier
}

export interface Readiness {
  /** ``ok`` 全部可用 · ``degraded`` 可服务但部分能力降级 · ``not_ready`` 关键依赖故障 */
  status: 'ok' | 'degraded' | 'not_ready'
  checks?: Record<string, ReadinessCheck>
  /** 关键依赖故障列表 —— 非空即不可服务 */
  critical_failed?: string[]
  /** 可选依赖不可用列表 —— 只影响对应能力，服务仍可用 */
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
