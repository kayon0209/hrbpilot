/** HRBP AI Workbench — Role guard component */

import { ReactNode } from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth';
import { Role } from '@/lib/constants';

interface RoleGuardProps {
  requiredRole: Role;
  children: ReactNode;
}

export function RoleGuard({ requiredRole, children }: RoleGuardProps) {
  const { checkAccess, isAuthenticated } = useAuth();

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  if (!checkAccess(requiredRole)) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center">
          <div className="text-page-title text-neutral-600">权限不足</div>
          <div className="text-body text-neutral-400 mt-2">当前角色无权访问此功能</div>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
