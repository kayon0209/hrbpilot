import { apiClient } from './http'

export interface DataSourceView {
  source_id: string
  name: string
  platform: string
  platform_label: string
  purpose: string
  authorized_scope: string
  content_types: string[]
  data_destination: string
  certification_level: number
  certification_label: string
  sync_status: string
  sync_label: string
  last_sync_at: string | null
  next_sync_at: string | null
  last_error: string | null
  paused: boolean
  revoked_at: string | null
  revoked_reason: string | null
  updated_at: string | null
}

export const PLATFORMS = [
  { value: 'feishu', label: '飞书' },
  { value: 'wecom', label: '企业微信' },
  { value: 'dingtalk', label: '钉钉' },
  { value: 'wps365', label: 'WPS 365' },
  { value: 'exchange', label: '企业邮箱（Microsoft 365）' },
  { value: 'oa', label: 'OA 系统' },
  { value: 'hris', label: 'HRIS 系统' },
] as const

export async function listDataSources() {
  return apiClient.request<{ sources: DataSourceView[] }>('/api/data-sources')
}

export function createDataSource(body: {
  name: string
  platform: string
  purpose: string
  authorized_scope: string
  content_types: string[]
  data_destination: string
}) {
  return apiClient.request<DataSourceView>('/api/data-sources', { method: 'POST', body: JSON.stringify(body) })
}

export function pauseDataSource(sourceId: string) {
  return apiClient.request<DataSourceView>(`/api/data-sources/${sourceId}/pause`, { method: 'POST', body: '{}' })
}

export function resumeDataSource(sourceId: string) {
  return apiClient.request<DataSourceView>(`/api/data-sources/${sourceId}/resume`, { method: 'POST', body: '{}' })
}

export function revokeDataSource(sourceId: string, reason: string) {
  return apiClient.request<DataSourceView>(`/api/data-sources/${sourceId}/revoke`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  })
}
