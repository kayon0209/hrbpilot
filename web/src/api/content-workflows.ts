import { apiClient } from './http'
import type { UnknownRecord } from './types'

export interface WeeklyGenerateInput { period: string; source_ids: string[]; draft_mode: boolean }
export const generateWeeklyReport = (input: WeeklyGenerateInput) => apiClient.request<{ report_id: string; report: UnknownRecord; is_draft: boolean }>('/api/weekly-report/generate', { method: 'POST', body: JSON.stringify(input) })
export const saveWeeklyReport = (input: { report_id: string; action: 'save' | 'publish'; edits?: UnknownRecord }) => apiClient.request<{ status: string; action: string }>('/api/weekly-report/save', { method: 'POST', body: JSON.stringify(input) })
export const expandKeywords = (input: { keywords: string[]; tone: string }) => apiClient.request<{ original: string[]; expanded: string[]; categories: Record<string,string[]> }>('/api/culture-content/expand-keywords', { method: 'POST', body: JSON.stringify({ ...input, expand_keywords: true }) })
export const generateCultureContent = (input: { keywords: string[]; tone: string; expand_keywords: boolean }) => apiClient.request<{ content_id: string; content: UnknownRecord }>('/api/culture-content/generate', { method: 'POST', body: JSON.stringify(input) })
