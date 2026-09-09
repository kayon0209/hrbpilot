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
export const getInterviewRecords = (q: string, cursor = '', limit = 10) => apiClient.request<InterviewRecordsPage>(`/api/interview-digest/records?q=${encodeURIComponent(q)}&limit=${limit}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`)
export const getInterviewRecord = (recordId: string) => apiClient.request<InterviewRecordDetail>(`/api/interview-digest/records/${recordId}`)

export interface VoiceAnalysisMeta { employee_name?: string; channel?: string }
export const startVoiceAnalysis = (content: string, meta: VoiceAnalysisMeta = {}) => apiClient.request<{ task_id: string; status: string }>('/api/voice-insight/analyze', { method: 'POST', body: JSON.stringify({ content, document_ids: [], ...meta }) })
export const getVoiceProgress = (taskId: string) => apiClient.request<TaskProgress>(`/api/voice-insight/progress/${taskId}`)
export const getVoiceResult = (taskId: string) => apiClient.request<UnknownRecord>(`/api/voice-insight/report/${taskId}`)
export const getVoiceHistory = (limit = 20) => apiClient.request<{ reports: UnknownRecord[]; total: number }>(`/api/voice-insight/history?limit=${limit}`)
export interface VoiceEntriesPage { entries: MaterialSummary[]; total: number; next_cursor: string | null }
export interface VoiceEntryDetail { entry: MaterialSummary; raw_text: string | null; result: UnknownRecord | null }
export const getVoiceEntries = (q: string, cursor = '', limit = 10) => apiClient.request<VoiceEntriesPage>(`/api/voice-insight/entries?q=${encodeURIComponent(q)}&limit=${limit}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`)
export const getVoiceEntry = (entryId: string) => apiClient.request<VoiceEntryDetail>(`/api/voice-insight/entries/${entryId}`)
