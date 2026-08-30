import { apiClient } from './http'

export interface AdminUserView {
  user_id: string
  name: string
  email: string
  role: 'employee' | 'hrbp' | 'hr_manager' | 'admin'
  org_unit: string | null
}

export const listAdminUsers = () => apiClient.request<{ users: AdminUserView[] }>('/api/admin/users')
