import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, test, vi } from 'vitest'
import { AgentTasksSection } from '../../src/features/agent-tasks/AgentTasksSection'
import { useSessionStore } from '../../src/app/session-store'

const QUEUE = {
  ok: true,
  items: [
    {
      task_id: 'task-1',
      title: '把张三的试用期跟进转给李经理',
      task_type: 'case_followup',
      risk_level: 'medium',
      requester_name: '王 HR',
      approval_id: 'ap-1',
      case_id: 'case-1',
      updated_at: '2026-09-15T12:00:00Z',
      web_path: '/tasks?tab=agent&task_id=task-1',
    },
  ],
  user_message: '共 1 件待审批。',
}

const calls: Array<{ url: string; method: string; body?: unknown }> = []

function setup(role: 'hr_manager' | 'hrbp') {
  useSessionStore.setState({ user: { id: 'u1', name: '测试用户', email: 'u@test.com', role, tenant_id: 't' } })
  calls.length = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined })
      if (url.endsWith('/api/agent-tasks/approvals')) return Response.json(QUEUE)
      if (url.includes('/api/v1/hr-cases/case-1/approve')) return Response.json({ approval_id: 'ap-1', status: 'APPROVED' })
      if (url.includes('/api/v1/hr-cases/case-1/execute'))
        return Response.json({ approval_id: 'ap-1', execution_id: 'exec-1', grant_id: 'g-1' })
      // 其余（任务列表等）给空集，别让列表区出现干扰内容。
      return Response.json({ ok: true, items: [], total: 0, truncated: false, next_cursor: null })
    }),
  )
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <AgentTasksSection />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
  useSessionStore.getState().logout()
})

test('hr_manager 看到待审批队列，批准会顺序调用审批与执行两个接口', async () => {
  setup('hr_manager')

  fireEvent.click(await screen.findByRole('tab', { name: '待审批' }))
  const row = await screen.findByLabelText(/待审批：把张三的试用期跟进转给李经理/)
  expect(row).toBeVisible()
  expect(row.textContent).toContain('提交人：王 HR')
  expect(row.textContent).toContain('审批编号：ap-1')

  fireEvent.click(within(row).getByRole('button', { name: '批准并执行' }))

  await waitFor(() => expect(calls.filter(c => c.method === 'POST')).toHaveLength(2))
  const approve = calls.find(c => c.url.includes('/approve'))!
  const execute = calls.find(c => c.url.includes('/execute'))!
  expect(approve.body).toMatchObject({ approval_id: 'ap-1', decision: 'approve' })
  // 执行必须有幂等键 —— 后端用它防重复派发。
  expect(typeof (execute.body as { request_id: string }).request_id).toBe('string')
})

test('驳回必须先写理由，确认按钮在理由为空时不可用', async () => {
  setup('hr_manager')

  fireEvent.click(await screen.findByRole('tab', { name: '待审批' }))
  const row = await screen.findByLabelText(/待审批：把张三的试用期跟进转给李经理/)
  fireEvent.click(within(row).getByRole('button', { name: '驳回…' }))

  const confirm = within(row).getByRole('button', { name: '确认驳回' }) as HTMLButtonElement
  expect(confirm.disabled).toBe(true)

  fireEvent.change(within(row).getByLabelText(/驳回理由/), { target: { value: '缺少员工确认记录' } })
  expect(confirm.disabled).toBe(false)
  fireEvent.click(confirm)

  await waitFor(() => {
    const reject = calls.find(c => c.url.includes('/approve'))
    expect(reject?.body).toMatchObject({ approval_id: 'ap-1', decision: 'reject', reason: '缺少员工确认记录' })
  })
  // 驳回不触发执行 —— 决定与执行是两件事。
  expect(calls.some(c => c.url.includes('/execute'))).toBe(false)
})

test('hrbp 看不到待审批入口（后端同样有角色门，这里不摆必然失败的按钮）', async () => {
  setup('hrbp')
  await screen.findByRole('tab', { name: '待我处理（0）' })
  expect(screen.queryByRole('tab', { name: '待审批' })).toBeNull()
})
