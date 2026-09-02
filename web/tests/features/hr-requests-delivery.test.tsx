import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import { HrRequestsPage } from '../../src/features/hr-requests/HrRequestsPage'
import { useSessionStore } from '../../src/app/session-store'

const baseRequest = {
  request_id: 'request-1', request_type: 'other', request_type_label: '其他事项', title: '调休申请',
  status: 'in_progress', status_label: '处理中', next_step: '明天回复', needs_materials: null,
  updated_at: null, created_at: '2026-09-02T09:00:00Z', description: '申请调休', hr_note: null,
  hr_case_id: null, hr_owner_id: 'hr-1', connector_source_label: '企业微信 · HR 服务',
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><HrRequestsPage /></QueryClientProvider>)
}

function installApi(delivery: Record<string, unknown>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    const method = init?.method ?? 'GET'
    if (path === '/api/hr-requests' && method === 'GET') return Response.json({ requests: [{ ...baseRequest, delivery }] })
    if (path.includes('/delivery-attempts/attempt-1/retry') && method === 'POST') return Response.json({ ...delivery, status: 'simulated_accepted', retryable: false })
    return Response.json({ message: `Unexpected ${method} ${path}` }, { status: 500 })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  cleanup()
  useSessionStore.getState().logout()
  vi.restoreAllMocks()
})

test('labels simulator acceptance as local only and never as a real WeCom send', async () => {
  useSessionStore.setState({ user: { id: 'hr-1', name: 'HR', email: 'hr@test.com', role: 'hrbp', tenant_id: 't' } })
  installApi({ attempt_id: 'attempt-1', status: 'simulated_accepted', attempt_count: 1, provider_msgid: 'sim-wecom-1', safe_message: '明天回复', retryable: false, error: null })
  renderPage()

  expect(await screen.findByText('企微协议本地模拟：已接受（未出网）')).toBeInTheDocument()
  expect(screen.queryByText(/已发送到企业微信/)).not.toBeInTheDocument()
})

test('allows HR to retry only a retryable local simulation failure', async () => {
  useSessionStore.setState({ user: { id: 'hr-1', name: 'HR', email: 'hr@test.com', role: 'hrbp', tenant_id: 't' } })
  const fetchMock = installApi({ attempt_id: 'attempt-1', status: 'retryable_failed', attempt_count: 1, provider_msgid: null, safe_message: '明天回复', retryable: true, error: '本地协议模拟暂时不可用，可重试' })
  renderPage()
  const user = userEvent.setup()

  await user.click(await screen.findByRole('button', { name: '重试模拟发送' }))
  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining('/api/hr-requests/request-1/delivery-attempts/attempt-1/retry'),
    expect.objectContaining({ method: 'POST' }),
  )
})
