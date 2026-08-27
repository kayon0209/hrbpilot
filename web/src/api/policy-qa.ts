import { apiClient, authHeaders, ApiError } from './http'
import type { KnowledgeBaseSummary } from './types'

export interface PolicySource { document_name: string; section?: string; content_snippet: string; confidence?: number }
export type PolicyStreamEvent =
  | { type: 'delta'; data: { text: string } }
  | { type: 'citation'; data: PolicySource[] }
  | { type: 'correction'; data: { full_text: string } }
  | { type: 'complete'; data: { message_id?: string; confidence?: number; has_evidence?: boolean; latency_ms?: number } }
  | { type: 'error'; data: { message: string } }

export async function listPolicyKnowledgeBases() {
  const result = await apiClient.request<{ knowledge_bases: KnowledgeBaseSummary[] }>('/api/policy-qa/knowledge-bases')
  return result.knowledge_bases
}

export async function streamPolicyAnswer(input: { question: string; kb_id?: string }, signal: AbortSignal, onEvent: (event: PolicyStreamEvent) => void) {
  const response = await fetch(`${import.meta.env.VITE_API_BASE_URL ?? ''}/api/policy-qa/ask`, {
    method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify({ ...input, stream: true }), signal,
  })
  if (!response.ok || !response.body) throw new ApiError(response.status, `问答请求失败（${response.status}）`)
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''
  while (true) {
    const { done, value } = await reader.read(); buffer += decoder.decode(value, { stream: !done })
    const frames = buffer.split(/\r?\n\r?\n/); buffer = frames.pop() ?? ''
    for (const frame of frames) {
      const line = frame.split(/\r?\n/).find(part => part.startsWith('data:'))
      if (!line) continue
      const outer = JSON.parse(line.slice(5).trim()) as { event: string; data: string }
      const data = JSON.parse(outer.data) as never
      const type = outer.event === 'chunk' ? 'delta' : outer.event === 'sources' ? 'citation' : outer.event === 'done' ? 'complete' : outer.event
      onEvent({ type, data } as PolicyStreamEvent)
    }
    if (done) break
  }
}

export const submitPolicyFeedback = (message_id: string, rating: 'up' | 'down') => apiClient.request('/api/policy-qa/feedback', { method: 'POST', body: JSON.stringify({ message_id, rating }) })
