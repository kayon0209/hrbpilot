/** HRBP AI Workbench — route definitions with RBAC guards */

import { createBrowserRouter, Navigate } from 'react-router-dom';
import { Layout } from './Layout';
import { DashboardPage } from '@/pages/Dashboard/DashboardPage';
import { PolicyQAPage } from '@/pages/PolicyQA/PolicyQAPage';
import { InterviewDigestPage } from '@/pages/InterviewDigest/InterviewDigestPage';
import { VoiceInsightPage } from '@/pages/VoiceInsight/VoiceInsightPage';
import { WeeklyReportPage } from '@/pages/WeeklyReport/WeeklyReportPage';
import { CultureContentPage } from '@/pages/CultureContent/CultureContentPage';
import { KBManagementPage } from '@/pages/KBManagement/KBManagementPage';
import { EvaluationPage } from '@/pages/Evaluation/EvaluationPage';
import { SettingsPage } from '@/pages/Settings/SettingsPage';
import { LoginPage } from '@/pages/Login/LoginPage';
import { RoleGuard } from '@/components/shared/RoleGuard';

export const router = createBrowserRouter([
  {
    path: '/login',
    element: <LoginPage />,
  },
  {
    path: '/',
    element: <Layout />,
    children: [
      { index: true, element: <Navigate to="/dashboard" replace /> },
      { path: 'dashboard', element: <DashboardPage /> },
      {
        path: 'policy-qa',
        element: <RoleGuard requiredRole="employee"><PolicyQAPage /></RoleGuard>,
      },
      {
        path: 'interview-digest',
        element: <RoleGuard requiredRole="hrbp"><InterviewDigestPage /></RoleGuard>,
      },
      {
        path: 'voice-insight',
        element: <RoleGuard requiredRole="hrbp"><VoiceInsightPage /></RoleGuard>,
      },
      {
        path: 'weekly-report',
        element: <RoleGuard requiredRole="hrbp"><WeeklyReportPage /></RoleGuard>,
      },
      {
        path: 'culture-content',
        element: <RoleGuard requiredRole="hrbp"><CultureContentPage /></RoleGuard>,
      },
      {
        path: 'kb-management',
        element: <RoleGuard requiredRole="hr_manager"><KBManagementPage /></RoleGuard>,
      },
      {
        path: 'evaluation',
        element: <RoleGuard requiredRole="hr_manager"><EvaluationPage /></RoleGuard>,
      },
      {
        path: 'settings',
        element: <RoleGuard requiredRole="admin"><SettingsPage /></RoleGuard>,
      },
    ],
  },
]);
