import { apiClient, authorizedFetch, normalizeError } from './http'
import type { KnowledgeBaseSummary } from './types'

export interface PolicySource { document_name: string; section?: string; content_snippet: string }
export interface PolicySession { session_id: string; title: string; updated_at: string | null }
export interface PolicySessionMessage { message_id: string; role: string; content: string; citations: PolicySource[] }
export type PolicyStreamEvent =
  | { type: 'delta'; data: { text: string } }
  | { type: 'citation'; data: PolicySource[] }
  | { type: 'correction'; data: { full_text: string } }
  // 'complete' carries session_id so follow-ups keep the same thread (spec §7.3);
  // confidence/latency stay internal — never surfaced to users (spec §11)
  | { type: 'complete'; data: { message_id?: string; session_id?: string; has_evidence?: boolean } }
  | { type: 'error'; data: { message: string } }

export async function listPolicyKnowledgeBases() {
  const result = await apiClient.request<{ knowledge_bases: KnowledgeBaseSummary[] }>('/api/policy-qa/knowledge-bases')
  return result.knowledge_bases
}

export async function listPolicySessions() {
  const result = await apiClient.request<{ sessions: PolicySession[] }>('/api/policy-qa/sessions')
  return result
}

export async function getPolicySessionMessages(sessionId: string) {
  return apiClient.request<{ session_id: string; messages: PolicySessionMessage[] }>(`/api/policy-qa/sessions/${sessionId}/messages`)
}

export async function streamPolicyAnswer(input: { question: string; kb_id?: string; session_id?: string }, signal: AbortSignal, onEvent: (event: PolicyStreamEvent) => void) {
  const response = await authorizedFetch('/api/policy-qa/ask', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...input, stream: true }), signal,
  })
  if (!response.ok || !response.body) throw await normalizeError(response)
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

export const submitPolicyFeedback = (message_id: string, rating: 'up' | 'down', correction?: string) => apiClient.request('/api/policy-qa/feedback', { method: 'POST', body: JSON.stringify({ message_id, rating, correction }) })
