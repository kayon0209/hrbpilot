/** HRBP AI Workbench — Global layout shell (Sidebar + TopBar + Main) */

import { Outlet, Navigate } from 'react-router-dom';
import { Sidebar } from '../components/layout/Sidebar';
import { TopBar } from '../components/layout/TopBar';
import { useAuth } from '../hooks/useAuth';

export function Layout() {
  const { isAuthenticated } = useAuth();

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  return (
    <div className="flex h-screen bg-[var(--color-bg-tertiary)]">
      {/* Sidebar */}
      <Sidebar />

      {/* Main area */}
      <div className="flex-1 flex flex-col min-w-0">
        <TopBar />
        <main className="flex-1 overflow-auto p-[var(--content-padding)]">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
