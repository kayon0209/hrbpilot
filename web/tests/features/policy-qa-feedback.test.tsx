/**
 * Policy QA feedback-chain regressions (audit 2026-08-31 P1-6).
 *
 * 1. Feedback failure must be visible and retryable — never a fake success.
 * 2. A replayed historical answer restores its message identity so feedback
 *    works after resume; without it the buttons silently did nothing.
 * 3. Stopping a stream mid-answer marks the answer as incomplete.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, test, vi, beforeEach } from 'vitest'
import { PolicyQaPage } from '../../src/features/policy-qa/PolicyQaPage'
import {
  getPolicySessionMessages,
  listPolicyKnowledgeBases,
  listPolicySessions,
  streamPolicyAnswer,
  submitPolicyFeedback,
} from '../../src/api/policy-qa'

const mockedSubmitFeedback = vi.mocked(submitPolicyFeedback)
const mockedStreamAnswer = vi.mocked(streamPolicyAnswer)
const mockedListSessions = vi.mocked(listPolicySessions)
const mockedGetMessages = vi.mocked(getPolicySessionMessages)
const mockedListKbs = vi.mocked(listPolicyKnowledgeBases)

vi.mock('../../src/api/policy-qa', async importOriginal => {
  const actual = await importOriginal<typeof import('../../src/api/policy-qa')>()
  return {
    ...actual,
    listPolicyKnowledgeBases: vi.fn(),
    listPolicySessions: vi.fn(),
    getPolicySessionMessages: vi.fn(),
    streamPolicyAnswer: vi.fn(),
    submitPolicyFeedback: vi.fn(),
  }
})

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={['/policy']}>
      <QueryClientProvider client={client}>
        <PolicyQaPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

describe('policy QA feedback chain (audit P1-6)', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    // Fresh defaults per test; implementations from earlier tests must not leak.
    mockedListKbs.mockResolvedValue([{ id: 'kb-1', name: '默认知识库' }] as never)
    mockedListSessions.mockResolvedValue({ sessions: [] } as never)
    mockedGetMessages.mockResolvedValue({ session_id: 's-1', messages: [] } as never)
  })

  test('feedback failure shows a visible error instead of a fake 已记录', async () => {
    mockedStreamAnswer.mockImplementation(
      async (_input: unknown, _signal: AbortSignal, onEvent: (e: unknown) => void) => {
        onEvent({ type: 'delta', data: { text: '答案' } })
        onEvent({ type: 'complete', data: { message_id: 'm-1', session_id: 's-1', has_evidence: true } })
      },
    )
    mockedSubmitFeedback.mockRejectedValue(new Error('网络中断'))

    renderPage()
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('问题'), '年假有几天？')
    await user.click(screen.getByRole('button', { name: '发送问题' }))
    await screen.findByRole('button', { name: '有帮助' })

    await user.click(screen.getByRole('button', { name: '有帮助' }))

    // The request must be awaited (not void-ed) and the failure surfaced.
    expect(mockedSubmitFeedback).toHaveBeenCalledExactlyOnceWith('m-1', 'up', undefined)
    expect(await screen.findByText(/反馈未送达/)).toBeInTheDocument()
    expect(screen.queryByText(/已记录/)).not.toBeInTheDocument()
  })

  test('a replayed historical answer restores message identity for feedback', async () => {
    mockedListSessions.mockResolvedValue({
      sessions: [{ session_id: 's-1', title: '年假有几天？', updated_at: '2026-08-31T02:00:00Z' }],
    } as never)
    mockedGetMessages.mockResolvedValue({
      session_id: 's-1',
      messages: [
        { message_id: 'm-hist', role: 'assistant', content: '历史答案', citations: [{ document_name: '员工手册.pdf', content_snippet: '片段' }] },
      ] as never,
    } as never)

    renderPage()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /年假有几天？/ }))
    await screen.findByText('历史答案')

    await user.click(screen.getByRole('button', { name: '有帮助' }))

    // Feedback on the resumed answer uses the replayed message identity.
    expect(mockedSubmitFeedback).toHaveBeenCalledExactlyOnceWith('m-hist', 'up', undefined)
    expect(await screen.findByText(/已记录/)).toBeInTheDocument()
  })

  test('stopping mid-stream marks the partial answer as incomplete', async () => {
    mockedStreamAnswer.mockImplementation(
      async (_input: unknown, signal: AbortSignal, onEvent: (e: unknown) => void) => {
        onEvent({ type: 'delta', data: { text: '这是已经生成的半截答案，' } })
        // Simulate a long stream: abort only resolves when the user clicks stop.
        await new Promise<void>(resolve => {
          if (signal.aborted) return resolve()
          signal.addEventListener('abort', () => resolve())
        })
      },
    )

    renderPage()
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('问题'), '详细说明')
    await user.click(screen.getByRole('button', { name: '发送问题' }))
    await screen.findByText(/半截答案/)
    await user.click(screen.getByRole('button', { name: '停止' }))

    // The partial answer stays visible WITH the incomplete warning.
    expect(await screen.findByText(/回答未完整生成/)).toBeInTheDocument()
    expect(screen.getByText(/半截答案/)).toBeInTheDocument()
  })
})
