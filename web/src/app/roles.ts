import type { UserRole } from '../api/types'

/**
 * Capability-based authorization (spec §3.2) — mirrors the backend
 * ROLE_CAPABILITIES matrix in app/access/middleware/rbac.py.
 *
 * No linear hierarchy: a role holds a SET of capabilities; admin does NOT
 * inherit HR business content access. When these diverge from the backend
 * matrix, the backend wins (this file only shapes navigation and redirects).
 */

export type Capability =
  | 'policy_qa'
  | 'interview_digest'
  | 'voice_insight'
  | 'weekly_report'
  | 'culture_content'
  | 'hr_case'
  | 'kb_management'
  | 'knowledge_feedback'
  | 'hr_request_triage'
  | 'employee_request'
  | 'work_summary'
  | 'evaluation'
  | 'settings'
  | 'audit_read'
  | 'user_admin'
  | 'data_source_admin'

const ROLE_CAPABILITIES: Record<string, Capability[]> = {
  employee: ['policy_qa', 'employee_request'],
  hrbp: [
    'policy_qa',
    'interview_digest',
    'voice_insight',
    'weekly_report',
    'culture_content',
    'hr_case',
    'hr_request_triage',
    'work_summary',
  ],
  hr_manager: [
    'policy_qa',
    'interview_digest',
    'voice_insight',
    'weekly_report',
    'culture_content',
    'hr_case',
    'knowledge_feedback',
    'hr_request_triage',
    'work_summary',
  ],
  admin: ['kb_management', 'evaluation', 'settings', 'audit_read', 'user_admin', 'data_source_admin'],
}

export function hasCapability(role: UserRole | null | undefined, capability: Capability): boolean {
  const caps = ROLE_CAPABILITIES[role ?? 'employee'] ?? []
  return caps.includes(capability)
}

/** Legacy helper kept for pages still gating on "is at least an HR business role". */
export function hasMinimumRole(role: UserRole | null | undefined, minimum: 'hrbp' | 'hr_manager' | 'admin'): boolean {
  if (minimum === 'hrbp') return hasCapability(role, 'interview_digest')
  if (minimum === 'hr_manager') return hasCapability(role, 'knowledge_feedback')
  return hasCapability(role, 'settings')
}

const pathCapabilities: Record<string, Capability> = {
  '/admin': 'settings', // any admin-capability page marks the admin experience
  '/data-sources': 'data_source_admin',
  '/interview': 'interview_digest',
  '/voice': 'voice_insight',
  '/weekly': 'weekly_report',
  '/culture': 'culture_content',
  '/knowledge': 'knowledge_feedback',
  '/knowledge-base': 'kb_management',
  '/evaluation': 'evaluation',
  '/settings': 'settings',
  '/audit': 'audit_read',
  '/users': 'user_admin',
  '/team': 'knowledge_feedback',
  '/tasks': 'work_summary',
  '/hr-requests': 'hr_request_triage',
  '/my-requests': 'employee_request',
}

/**
 * Paths reachable per role that are not in `pathCapabilities`.
 * Unlisted paths must NOT default to allow: an admin carrying a stale
 * `from=/policy` after a role switch would sail past this check and hit the
 * route role gate (forbidden). Mirror the router's role lists exactly.
 */
const rolePaths: Record<string, string[]> = {
  employee: ['/policy', '/my-requests'],
  hrbp: ['/', '/policy', '/tasks', '/interview', '/voice', '/weekly', '/culture', '/hr-requests'],
  hr_manager: ['/', '/policy', '/tasks', '/interview', '/voice', '/weekly', '/culture', '/team', '/knowledge', '/hr-requests'],
  admin: ['/admin', '/users', '/evaluation', '/knowledge-base', '/data-sources', '/settings', '/audit'],
}

/** Whether a role may open a path — used to validate post-login redirects. */
export function canAccessPath(role: UserRole | null | undefined, path: string) {
  const clean = path.split('?')[0]
  const required = pathCapabilities[clean]
  if (required) return hasCapability(role, required)
  return (rolePaths[role ?? 'employee'] ?? []).includes(clean)
}
