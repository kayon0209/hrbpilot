import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useSessionStore } from './session-store'

export function ProtectedRoute({ children, roles }: { children: ReactNode; roles?: string[] }) {
  const { user, pending } = useSessionStore()
  const location = useLocation()
  if (pending) return <main className="center-state" aria-busy="true">正在恢复会话…</main>
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  if (roles && !roles.includes(user.role)) {
    return <Navigate to="/forbidden" replace state={{ from: location.pathname, requiredRoles: roles }} />
  }
  return children
}
