import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test } from 'vitest'
import { InterviewDigestPage } from '../../src/features/interview/InterviewDigestPage'
import { CultureContentPage } from '../../src/features/culture/CultureContentPage'
import { useSessionStore } from '../../src/app/session-store'

function renderPage(page: React.ReactNode) { return render(<QueryClientProvider client={new QueryClient()}>{page}</QueryClientProvider>) }
afterEach(() => useSessionStore.getState().logout())

test('blocks interview analysis shorter than 50 characters', async () => {
  useSessionStore.setState({ user: { id: '1', name: 'HR', email: 'hr@test.com', role: 'hrbp', tenant_id: 't' } })
  renderPage(<InterviewDigestPage />)
  await userEvent.type(screen.getByLabelText('面谈内容'), '太短')
  await userEvent.click(screen.getByRole('button', { name: '开始分析' }))
  expect(screen.getByRole('alert')).toHaveTextContent('至少需要50字')
})

test('keeps keyword expansion separate from content generation', () => {
  useSessionStore.setState({ user: { id: '1', name: 'HR', email: 'hr@test.com', role: 'hrbp', tenant_id: 't' } })
  renderPage(<CultureContentPage />)
  expect(screen.getByRole('button', { name: '扩展关键词' })).toBeVisible()
  expect(screen.getByRole('button', { name: '生成内容' })).toBeVisible()
})
