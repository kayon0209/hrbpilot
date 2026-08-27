import { apiClient } from './http'
import type { KnowledgeBaseSummary } from './types'

export type DocumentStatus = 'uploaded' | 'parsing' | 'indexed' | 'error'
export interface KbDocument { id: string; kb_id: string; filename: string; file_type: string; size_bytes: number; status: DocumentStatus; error_message?: string | null; chunk_count: number; indexed_at?: string | null; created_at?: string }
export const createKnowledgeBase = (input: { name: string; description?: string; scenario_id?: string }) => apiClient.request<KnowledgeBaseSummary>('/api/kb/create', { method: 'POST', body: JSON.stringify({ ...input, scenario_id: input.scenario_id ?? 'policy_qa' }) })
export const listKnowledgeBases = async () => (await apiClient.request<{ knowledge_bases: KnowledgeBaseSummary[] }>('/api/kb/list')).knowledge_bases
export const getKnowledgeBase = (id: string) => apiClient.request<KnowledgeBaseSummary>(`/api/kb/${id}`)
export const uploadDocument = (kbId: string, file: File) => { const body = new FormData(); body.set('file', file); return apiClient.request<KbDocument>(`/api/kb/${kbId}/upload`, { method: 'POST', body }) }
export const triggerIngestion = (kbId: string) => apiClient.request<{ task_id: string | null; status: string; message?: string }>(`/api/kb/${kbId}/ingest`, { method: 'POST' })
export const listDocuments = async (kbId: string) => (await apiClient.request<{ documents: KbDocument[] }>(`/api/kb/${kbId}/documents`)).documents
export const deleteDocument = (kbId: string, docId: string) => apiClient.request(`/api/kb/${kbId}/documents/${docId}`, { method: 'DELETE' })
export const deleteKnowledgeBase = (kbId: string) => apiClient.request('/api/kb/delete', { method: 'POST', body: JSON.stringify({ kb_id: kbId }) })
