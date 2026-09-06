import { apiClient } from './http'

export interface AdminUserView {
  user_id: string
  name: string
  email: string
  role: 'employee' | 'hrbp' | 'hr_manager' | 'admin'
  org_unit_id: string | null
  org_unit: string | null
  manager_scope_org_unit_ids: string[]
}

export interface OrgUnitView {
  org_unit_id: string
  name: string
  parent_id: string | null
}

export interface AdminUsersInventory {
  users: AdminUserView[]
  org_units: OrgUnitView[]
}

export interface LegacyWorkView {
  work_id: string
  work_type: 'async_task' | 'weekly_report' | 'knowledge_feedback_candidate' | 'culture_content'
  title: string
}

export interface LegacyWorkInventory {
  items: LegacyWorkView[]
  total: number
}

export const listAdminUsers = () => apiClient.request<AdminUsersInventory>('/api/admin/users')

export const listLegacyWork = () => apiClient.request<LegacyWorkInventory>('/api/admin/users/legacy-work')

export const claimLegacyWork = (work: LegacyWorkView, userId: string) => apiClient.request<{
  work_id: string
  work_type: LegacyWorkView['work_type']
  owner_user_id: string
}>(`/api/admin/users/legacy-work/${work.work_type}/${work.work_id}/owner`, {
  method: 'PUT',
  body: JSON.stringify({ user_id: userId }),
})

export const createOrgUnit = (name: string) => apiClient.request<OrgUnitView>('/api/admin/users/org-units', {
  method: 'POST',
  body: JSON.stringify({ name }),
})

export const assignUserOrgUnit = (userId: string, orgUnitId: string | null) => apiClient.request<{
  user_id: string
  org_unit_id: string | null
  org_unit: string | null
}>(`/api/admin/users/${userId}/org-unit`, {
  method: 'PUT',
  body: JSON.stringify({ org_unit_id: orgUnitId }),
})

export const replaceManagerScopes = (managerId: string, orgUnitIds: string[]) => apiClient.request<{
  manager_id: string
  org_unit_ids: string[]
}>(`/api/admin/users/${managerId}/manager-scopes`, {
  method: 'PUT',
  body: JSON.stringify({ org_unit_ids: orgUnitIds }),
})
