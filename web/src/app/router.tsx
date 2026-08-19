/* eslint-disable react-refresh/only-export-components */
import { Navigate, Outlet, createBrowserRouter, createMemoryRouter, type RouteObject } from 'react-router-dom'
import { ProtectedRoute } from './ProtectedRoute'
import { AppShell } from '../components/AppShell'
import { LoginPage } from '../features/auth/LoginPage'
import { OverviewPage } from '../pages/OverviewPage'
import { PolicyQaPage } from '../features/policy-qa/PolicyQaPage'
import { KnowledgeBasePage } from '../features/knowledge-base/KnowledgeBasePage'
import { InterviewDigestPage } from '../features/interview/InterviewDigestPage'
import { VoiceInsightPage } from '../features/voice/VoiceInsightPage'
import { WeeklyReportPage } from '../features/weekly/WeeklyReportPage'
import { CultureContentPage } from '../features/culture/CultureContentPage'
import { EvaluationPage } from '../features/evaluation/EvaluationPage'
import { SettingsPage } from '../features/settings/SettingsPage'
import { ForbiddenPage } from '../pages/ForbiddenPage'

function ProtectedLayout() {
  // Authenticated shell only — per-route role gating happens on each child route.
  return (
    <ProtectedRoute>
      <AppShell>
        <Outlet />
      </AppShell>
    </ProtectedRoute>
  )
}

const EMPLOYEE_AND_ABOVE = ['employee', 'hrbp', 'hr_manager', 'admin']
const HRBP_AND_ABOVE = ['hrbp', 'hr_manager', 'admin']
const HR_MANAGER_AND_ABOVE = ['hr_manager', 'admin']
const ADMIN_ONLY = ['admin']

export const routes: RouteObject[] = [
  { path: '/login', element: <LoginPage /> },
  {
    element: <ProtectedLayout />,
    children: [
      { index: true, element: <OverviewPage /> },
      { path: 'policy', element: <ProtectedRoute roles={EMPLOYEE_AND_ABOVE}><PolicyQaPage /></ProtectedRoute> },
      { path: 'knowledge', element: <ProtectedRoute roles={HR_MANAGER_AND_ABOVE}><KnowledgeBasePage /></ProtectedRoute> },
      { path: 'interview', element: <ProtectedRoute roles={HRBP_AND_ABOVE}><InterviewDigestPage /></ProtectedRoute> },
      { path: 'voice', element: <ProtectedRoute roles={HRBP_AND_ABOVE}><VoiceInsightPage /></ProtectedRoute> },
      { path: 'weekly', element: <ProtectedRoute roles={HRBP_AND_ABOVE}><WeeklyReportPage /></ProtectedRoute> },
      { path: 'culture', element: <ProtectedRoute roles={HRBP_AND_ABOVE}><CultureContentPage /></ProtectedRoute> },
      { path: 'evaluation', element: <ProtectedRoute roles={HR_MANAGER_AND_ABOVE}><EvaluationPage /></ProtectedRoute> },
      { path: 'settings', element: <ProtectedRoute roles={ADMIN_ONLY}><SettingsPage /></ProtectedRoute> },
    ],
  },
  { path: '/forbidden', element: <ForbiddenPage /> },
  { path: '*', element: <Navigate to="/" replace /> },
]

export const createAppRouter = (initialEntries?: string[]) =>
  initialEntries ? createMemoryRouter(routes, { initialEntries }) : createBrowserRouter(routes)
