import { apiClient } from './http'
import type { UnknownRecord } from './types'

export interface TaskProgress { task_id: string; status: string; progress: number; error?: string | null }
export const uploadInterviewDocument = (file: File) => { const body = new FormData(); body.set('file', file); return apiClient.request<{ filename: string; content: string; text_length: number }>('/api/interview-digest/upload', { method: 'POST', body }) }
export const startInterviewAnalysis = (content: string) => apiClient.request<{ task_id: string; status: string }>('/api/interview-digest/analyze', { method: 'POST', body: JSON.stringify({ content }) })
export const getInterviewProgress = (taskId: string) => apiClient.request<TaskProgress>(`/api/interview-digest/progress/${taskId}`)
export const getInterviewResult = (taskId: string) => apiClient.request<UnknownRecord>(`/api/interview-digest/result/${taskId}`)
export const getInterviewHistory = () => apiClient.request<{ digests: UnknownRecord[]; total: number }>('/api/interview-digest/history')
export const startVoiceAnalysis = (content: string) => apiClient.request<{ task_id: string; status: string }>('/api/voice-insight/analyze', { method: 'POST', body: JSON.stringify({ content, document_ids: [] }) })
export const getVoiceProgress = (taskId: string) => apiClient.request<TaskProgress>(`/api/voice-insight/progress/${taskId}`)
export const getVoiceResult = (taskId: string) => apiClient.request<UnknownRecord>(`/api/voice-insight/report/${taskId}`)
export const getVoiceHistory = () => apiClient.request<{ reports: UnknownRecord[]; total: number }>('/api/voice-insight/history')
