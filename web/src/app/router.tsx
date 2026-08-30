/* eslint-disable react-refresh/only-export-components */
import { Navigate, Outlet, createBrowserRouter, createMemoryRouter, type RouteObject } from 'react-router-dom'
import { ProtectedRoute } from './ProtectedRoute'
import { AppShell } from '../components/AppShell'
import { LoginPage } from '../features/auth/LoginPage'
import { TodayPage } from '../pages/TodayPage'
import { AdminHomePage } from '../pages/AdminHomePage'
import { PolicyQaPage } from '../features/policy-qa/PolicyQaPage'
import { KnowledgeBasePage } from '../features/knowledge-base/KnowledgeBasePage'
import { KnowledgeFeedbackPage } from '../features/knowledge-feedback/KnowledgeFeedbackPage'
import { InterviewDigestPage } from '../features/interview/InterviewDigestPage'
import { VoiceInsightPage } from '../features/voice/VoiceInsightPage'
import { WeeklyReportPage } from '../features/weekly/WeeklyReportPage'
import { CultureContentPage } from '../features/culture/CultureContentPage'
import { EvaluationPage } from '../features/evaluation/EvaluationPage'
import { SettingsPage } from '../features/settings/SettingsPage'
import { DataSourcesPage } from '../features/data-sources/DataSourcesPage'
import { TeamPendingPage } from '../features/team/TeamPendingPage'
import { HrRequestsPage } from '../features/hr-requests/HrRequestsPage'
import { MyRequestsPage } from '../features/requests/MyRequestsPage'
import { TasksPage } from '../features/tasks/TasksPage'
import { AuditPage } from '../features/audit/AuditPage'
import { AdminUsersPage } from '../features/admin-users/AdminUsersPage'
import { ForbiddenPage } from '../pages/ForbiddenPage'
import { useSessionStore } from './session-store'
import type { UserRole } from '../api/types'

function ProtectedLayout() {
  // Authenticated shell only — per-route capability gating happens on each child route.
  return (
    <ProtectedRoute>
      <AppShell>
        <Outlet />
      </AppShell>
    </ProtectedRoute>
  )
}

/** Gate a route by exact roles (no hierarchy — spec §3.2). */
const only = (...roles: UserRole[]) => roles

/** '/': send each experience to its own home. */
/**
 * '/': the HRBP/manager workspace renders directly. employee and admin are
 * redirected to their own experience homes (a self-redirect here would loop).
 */
function RoleHome() {
  const role = useSessionStore(state => state.user?.role ?? null)
  if (role === 'employee') return <Navigate to="/policy" replace />
  if (role === 'admin') return <Navigate to="/admin" replace />
  return <TodayPage />
}

export const routes: RouteObject[] = [
  { path: '/login', element: <LoginPage /> },
  {
    element: <ProtectedLayout />,
    children: [
      { index: true, element: <RoleHome /> },
      { path: 'admin', element: <ProtectedRoute roles={only('admin')}><AdminHomePage /></ProtectedRoute> },
      { path: 'policy', element: <ProtectedRoute roles={only('employee', 'hrbp', 'hr_manager')}><PolicyQaPage /></ProtectedRoute> },
      { path: 'knowledge', element: <ProtectedRoute roles={only('hr_manager')}><KnowledgeFeedbackPage /></ProtectedRoute> },
      { path: 'knowledge-base', element: <ProtectedRoute roles={only('admin')}><KnowledgeBasePage /></ProtectedRoute> },
      { path: 'interview', element: <ProtectedRoute roles={only('hrbp', 'hr_manager')}><InterviewDigestPage /></ProtectedRoute> },
      { path: 'voice', element: <ProtectedRoute roles={only('hrbp', 'hr_manager')}><VoiceInsightPage /></ProtectedRoute> },
      { path: 'weekly', element: <ProtectedRoute roles={only('hrbp', 'hr_manager')}><WeeklyReportPage /></ProtectedRoute> },
      { path: 'culture', element: <ProtectedRoute roles={only('hrbp', 'hr_manager')}><CultureContentPage /></ProtectedRoute> },
      { path: 'team', element: <ProtectedRoute roles={only('hr_manager')}><TeamPendingPage /></ProtectedRoute> },
      { path: 'hr-requests', element: <ProtectedRoute roles={only('hrbp', 'hr_manager')}><HrRequestsPage /></ProtectedRoute> },
      { path: 'tasks', element: <ProtectedRoute roles={only('hrbp', 'hr_manager')}><TasksPage /></ProtectedRoute> },
      { path: 'evaluation', element: <ProtectedRoute roles={only('admin')}><EvaluationPage /></ProtectedRoute> },
      { path: 'data-sources', element: <ProtectedRoute roles={only('admin')}><DataSourcesPage /></ProtectedRoute> },
      { path: 'settings', element: <ProtectedRoute roles={only('admin')}><SettingsPage /></ProtectedRoute> },
      { path: 'audit', element: <ProtectedRoute roles={only('admin')}><AuditPage /></ProtectedRoute> },
      { path: 'users', element: <ProtectedRoute roles={only('admin')}><AdminUsersPage /></ProtectedRoute> },
      { path: 'my-requests', element: <ProtectedRoute roles={only('employee')}><MyRequestsPage /></ProtectedRoute> },
    ],
  },
  { path: '/forbidden', element: <ForbiddenPage /> },
  { path: '*', element: <Navigate to="/" replace /> },
]

export const createAppRouter = (initialEntries?: string[]) =>
  initialEntries ? createMemoryRouter(routes, { initialEntries }) : createBrowserRouter(routes)
