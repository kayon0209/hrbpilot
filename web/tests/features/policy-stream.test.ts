import { afterEach, expect, test, vi } from 'vitest'
import { streamPolicyAnswer, type PolicyStreamEvent } from '../../src/api/policy-qa'

afterEach(() => vi.restoreAllMocks())

test('parses fragmented SSE frames into citations, deltas and completion', async () => {
  const encoder = new TextEncoder()
  const parts = [
    'data: {"event":"sources","data":"[{\\"document_name\\":\\"制度.txt\\",\\"content_snippet\\":\\"请假条款\\"}]"}\n',
    '\ndata: {"event":"chunk","data":"{\\"text\\":\\"需要审批\\"}"}\n\n',
    'data: {"event":"done","data":"{\\"confidence\\":0.9,\\"has_evidence\\":true}"}\n\n',
  ]
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(new ReadableStream({ start(controller) { parts.forEach(part => controller.enqueue(encoder.encode(part))); controller.close() } }), { status: 200 }))
  const events: PolicyStreamEvent[] = []
  await streamPolicyAnswer({ question: '如何请假', kb_id: 'kb-1' }, new AbortController().signal, event => events.push(event))
  expect(events.map(event => event.type)).toEqual(['citation', 'delta', 'complete'])
  expect(events[1]).toMatchObject({ type: 'delta', data: { text: '需要审批' } })
})
