import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import { InterviewDigestPage } from '../../src/features/interview/InterviewDigestPage'
import { CultureContentPage } from '../../src/features/culture/CultureContentPage'
import { useSessionStore } from '../../src/app/session-store'
import * as interviewApi from '../../src/api/async-scenarios'

function renderPage(page: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return { client, ...render(<QueryClientProvider client={client}>{page}</QueryClientProvider>) }
}
afterEach(() => {
  cleanup()
  useSessionStore.getState().logout()
  vi.restoreAllMocks()
})

test('blocks interview analysis shorter than 50 characters', async () => {
  useSessionStore.setState({ user: { id: '1', name: 'HR', email: 'hr@test.com', role: 'hrbp', tenant_id: 't' } })
  const history = vi.spyOn(interviewApi, 'getInterviewHistory').mockResolvedValue({ digests: [] })
  const { client } = renderPage(<InterviewDigestPage />)
  const user = userEvent.setup()
  await waitFor(() => expect(client.getQueryState(['interview-history'])?.status).toBe('success'))
  await user.type(screen.getByLabelText('面谈内容'), '太短')
  await user.click(screen.getByRole('button', { name: '开始分析' }))
  expect(screen.getByRole('alert')).toHaveTextContent('至少需要50字')
  expect(history).toHaveBeenCalledOnce()
})

test('keeps keyword expansion separate from content generation', async () => {
  useSessionStore.setState({ user: { id: '1', name: 'HR', email: 'hr@test.com', role: 'hrbp', tenant_id: 't' } })
  renderPage(<CultureContentPage />)
  await screen.findByRole('heading', { name: '渠道内容' })
  expect(screen.getByRole('button', { name: '扩展关键词' })).toBeVisible()
  expect(screen.getByRole('button', { name: '生成内容' })).toBeVisible()
})
