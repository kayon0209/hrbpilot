import type { UserRole } from '../api/types'

const levels: Record<string, number> = {
  employee: 0,
  hrbp: 1,
  hr_manager: 2,
  admin: 3,
}

export function hasMinimumRole(role: UserRole | null | undefined, minimum: keyof typeof levels) {
  return (levels[role ?? 'employee'] ?? 0) >= levels[minimum]
}
