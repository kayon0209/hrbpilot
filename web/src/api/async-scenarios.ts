import { apiClient } from './http'
import type { UnknownRecord } from './types'

// status is a stage word: pending(排队中) | running(正在分析) | completed | failed —
// single-shot LLM analysis has no measurable steps, so no percentage (spec §9.1)
export interface TaskProgress { task_id: string; status: string; error?: string | null }
export const uploadInterviewDocument = (file: File) => { const body = new FormData(); body.set('file', file); return apiClient.request<{ filename: string; content: string; text_length: number }>('/api/interview-digest/upload', { method: 'POST', body }) }

export interface InterviewAnalysisMeta { employee_name?: string; title?: string; interview_type?: string; interview_date?: string }
export const startInterviewAnalysis = (content: string, meta: InterviewAnalysisMeta = {}) => apiClient.request<{ task_id: string; status: string }>('/api/interview-digest/analyze', { method: 'POST', body: JSON.stringify({ content, ...meta }) })
export const getInterviewProgress = (taskId: string) => apiClient.request<TaskProgress>(`/api/interview-digest/progress/${taskId}`)
export const getInterviewResult = (taskId: string) => apiClient.request<UnknownRecord>(`/api/interview-digest/result/${taskId}`)
export const getInterviewHistory = (limit = 20) => apiClient.request<{ digests: UnknownRecord[]; total: number }>(`/api/interview-digest/history?limit=${limit}`)

// —— 材料库（摘要列表 + 按需详情，设计见 docs/data-scale-design-2026-09-09.md）——
export interface MaterialSummary {
  record_id?: string
  entry_id?: string
  employee_name: string | null
  title?: string | null
  interview_type?: string
  interview_date?: string | null
  channel?: string
  status: string
  risk_level?: string | null
  top_severity?: string | null
  cluster_count?: number
  summary: string
  confidence?: number | null
  raw_text_length: number
  has_result: boolean
  created_at: string | null
  completed_at: string | null
}
export interface InterviewRecordsPage { records: MaterialSummary[]; total: number; next_cursor: string | null }
export interface InterviewRecordDetail { record: MaterialSummary; raw_text: string | null; result: UnknownRecord | null }
export interface InterviewRecordsFilters { q?: string; interview_type?: string; status?: string; date_from?: string; date_to?: string }
export function getInterviewRecordsPaged(q: string, filters: InterviewRecordsFilters = {}, cursor = '', limit = 10) {
  const qs = new URLSearchParams(); qs.set('limit', String(limit)); if (q) qs.set('q', q)
  const t = filters.interview_type?.trim(); if (t) qs.set('interview_type', t)
  const s = filters.status?.trim(); if (s) qs.set('status', s)
  const df = filters.date_from?.trim(); if (df) qs.set('date_from', df)
  const dt = filters.date_to?.trim(); if (dt) qs.set('date_to', dt)
  if (cursor) qs.set('cursor', cursor)
  return apiClient.request<InterviewRecordsPage>(`/api/interview-digest/records?${qs.toString()}`)
}
// compat shim for existing call sites that pass (q, cursor)
export const getInterviewRecords = (q: string, cursor = '', limit = 10) => getInterviewRecordsPaged(q, {}, cursor, limit)
export const getInterviewRecord = (recordId: string) => apiClient.request<InterviewRecordDetail>(`/api/interview-digest/records/${recordId}`)

export interface VoiceAnalysisMeta { employee_name?: string; channel?: string }
export const startVoiceAnalysis = (content: string, meta: VoiceAnalysisMeta = {}) => apiClient.request<{ task_id: string; status: string }>('/api/voice-insight/analyze', { method: 'POST', body: JSON.stringify({ content, document_ids: [], ...meta }) })
export const getVoiceProgress = (taskId: string) => apiClient.request<TaskProgress>(`/api/voice-insight/progress/${taskId}`)
export const getVoiceResult = (taskId: string) => apiClient.request<UnknownRecord>(`/api/voice-insight/report/${taskId}`)
export const getVoiceHistory = (limit = 20) => apiClient.request<{ reports: UnknownRecord[]; total: number }>(`/api/voice-insight/history?limit=${limit}`)
export interface VoiceEntriesPage { entries: MaterialSummary[]; total: number; next_cursor: string | null }
export interface VoiceEntryDetail { entry: MaterialSummary; raw_text: string | null; result: UnknownRecord | null }
export interface VoiceEntriesFilters { channel?: string; status?: string }
export function getVoiceEntriesPaged(q: string, filters: VoiceEntriesFilters = {}, cursor = '', limit = 10) {
  const qs = new URLSearchParams(); qs.set('limit', String(limit)); if (q) qs.set('q', q)
  const ch = filters.channel?.trim(); if (ch) qs.set('channel', ch)
  const s = filters.status?.trim(); if (s) qs.set('status', s)
  if (cursor) qs.set('cursor', cursor)
  return apiClient.request<VoiceEntriesPage>(`/api/voice-insight/entries?${qs.toString()}`)
}
// compat shim
export const getVoiceEntries = (q: string, cursor = '', limit = 10) => getVoiceEntriesPaged(q, {}, cursor, limit)
export const getVoiceEntry = (entryId: string) => apiClient.request<VoiceEntryDetail>(`/api/voice-insight/entries/${entryId}`)

// —— 批量（P1 §5）：batch_id 进度 + 单条失败隔离重试 ——
export interface MaterialBatchView { batch_id: string; type: string; total: number; completed: number; failed: number; status: string; created_at?: string | null }
export const createMaterialBatch = (body: { type: 'interview' | 'voice'; items: Array<{ content: string; employee_name?: string; title?: string; interview_type?: string; interview_date?: string; channel?: string }> }) => apiClient.request<MaterialBatchView>('/api/material-batches', { method: 'POST', body: JSON.stringify(body) })
export const getMaterialBatch = (batchId: string) => apiClient.request<MaterialBatchView>(`/api/material-batches/${batchId}`)
export const retryMaterialBatch = (batchId: string) => apiClient.request<{ batch_id: string; retried: number }>(`/api/material-batches/${batchId}/retry-failed`, { method: 'POST' })
