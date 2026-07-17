/** HRBP AI Workbench — Auth hook */

import { useCallback } from 'react';
import { authStore } from '@/stores/authStore';
import { canAccess, Role } from '@/lib/constants';

export function useAuth() {
  const user = authStore((s) => s.user);
  const isAuthenticated = authStore((s) => s.isAuthenticated());
  const login = authStore((s) => s.login);
  const logout = authStore((s) => s.logout);
  const canAccessPage = authStore((s) => s.canAccessPage);

  const role: Role = user?.role || 'employee';
  const tenantId = user?.tenant_id || '';

  const checkAccess = useCallback(
    (requiredRole: Role) => canAccess(role, requiredRole),
    [role]
  );

  return {
    user,
    role,
    tenantId,
    isAuthenticated,
    login,
    logout,
    canAccessPage,
    checkAccess,
  };
}
