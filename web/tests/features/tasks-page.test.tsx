import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, test, vi } from 'vitest'
import { TasksPage } from '../../src/features/tasks/TasksPage'

function renderPage(payload: object) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith('/api/work-summaries/assignable-owners')) {
      return Response.json({ owners: [] })
    }
    return Response.json(payload)
  }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <TasksPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

afterEach(() => vi.unstubAllGlobals())

test('shows accountable owner, dependency, deadline and truthful unit progress', async () => {
  renderPage({
    continue_work: {
      work_id: 'case-1',
      work_type: 'hr_case',
      title: '处理员工关系案例',
      business_status: '处理中',
      next_action: '等待法务复核',
      resume_target: '/cases/case-1',
      updated_at: '2026-08-31T08:00:00Z',
      due_at: '2026-09-03T10:30:00Z',
      owner: '王经理',
      waiting_for: '法务团队',
      progress_mode: 'units',
      completed_units: 2,
      total_units: 5,
    },
    attention: [],
    completed_today: [],
  })

  const task = (await screen.findByText('处理员工关系案例')).closest('article')!
  expect(within(task).getByText('负责人：王经理')).toBeVisible()
  expect(within(task).getByText('等待：法务团队')).toBeVisible()
  // due_at is a UTC instant; the UI renders it in the local timezone (FE-01).
  const local = new Date('2026-09-03T10:30:00Z')
  const pad = (n: number) => String(n).padStart(2, '0')
  const expected = `截止：${local.getFullYear()}-${pad(local.getMonth() + 1)}-${pad(local.getDate())} ${pad(local.getHours())}:${pad(local.getMinutes())}`
  expect(within(task).getByText(expected)).toBeVisible()
  expect(within(task).getByText('进度：2/5')).toBeVisible()
  expect(within(task).queryByText('阶段：处理中')).not.toBeInTheDocument()
})

test('uses the business stage when no real progress denominator exists', async () => {
  renderPage({
    continue_work: {
      work_id: 'policy-1',
      work_type: 'policy_question',
      title: '核对休假制度',
      business_status: '等待确认',
      next_action: '确认引用范围',
      resume_target: '/policy',
      updated_at: null,
      due_at: null,
      owner: null,
      waiting_for: null,
      progress_mode: 'stage',
      completed_units: null,
      total_units: null,
    },
    attention: [],
    completed_today: [],
  })

  const task = (await screen.findByText('核对休假制度')).closest('article')!
  expect(within(task).getByText('阶段：等待确认')).toBeVisible()
  expect(within(task).queryByText(/进度：/)).not.toBeInTheDocument()
})

test('summarises todays completed output and the next concrete continuation', async () => {
  renderPage({
    continue_work: {
      work_id: 'report-1',
      work_type: 'weekly_report',
      title: '本周周报草稿',
      business_status: '待确认',
      next_action: '检查风险并确认发布',
      resume_target: '/weekly',
      updated_at: '2026-08-31T08:00:00Z',
      due_at: null,
      owner: '王经理',
      waiting_for: null,
      progress_mode: 'stage',
      completed_units: null,
      total_units: null,
    },
    attention: [],
    completed_today: [{
      work_id: 'interview-1',
      work_type: 'interview_digest',
      title: '面谈纪要已确认',
      business_status: '已完成',
      next_action: '已形成可复核纪要',
      resume_target: '/interview',
      updated_at: '2026-08-31T07:00:00Z',
      due_at: null,
      owner: '王经理',
      waiting_for: null,
      progress_mode: 'stage',
      completed_units: null,
      total_units: null,
    }],
  })

  expect(await screen.findByText('今天完成 1 项真实产出。')).toBeVisible()
  expect(screen.getByText('下一步：本周周报草稿 · 检查风险并确认发布')).toBeVisible()
})

test('creates a persistent multi-day task from the task workspace', async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith('/api/work-summaries/tasks') && init?.method === 'POST') {
      return Response.json({ task_id: 'task-1', title: '完成三地薪酬复核' }, { status: 201 })
    }
    return Response.json({ continue_work: null, attention: [], completed_today: [] })
  })
  vi.stubGlobal('fetch', fetchMock)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <TasksPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  fireEvent.change(await screen.findByLabelText('任务名称'), {
    target: { value: '完成三地薪酬复核' },
  })
  fireEvent.change(screen.getByLabelText('下一步'), {
    target: { value: '先核对华东数据' },
  })
  fireEvent.click(screen.getByRole('button', { name: '创建多日任务' }))

  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining('/api/work-summaries/tasks'),
    expect.objectContaining({ method: 'POST' }),
  ))
})


test('edits a task and PATCHes waiting-for, deadline and owner together', async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/api/work-summaries/assignable-owners')) {
      return Response.json({ owners: [{ user_id: 'u-1', name: '王经理' }, { user_id: 'u-2', name: '李经理' }] })
    }
    if (url.includes('/api/work-summaries/tasks/task-9') && init?.method === 'PATCH') {
      return Response.json({ task_id: 'task-9', title: '完成三地薪酬复核' })
    }
    return Response.json({
      continue_work: {
        work_id: 'task-9',
        work_type: 'work_task',
        title: '完成三地薪酬复核',
        business_status: '处理中',
        next_action: '先核对华东数据',
        resume_target: '/tasks',
        updated_at: '2026-08-31T08:00:00Z',
        due_at: null,
        owner: null,
        waiting_for: null,
        progress_mode: 'stage',
        completed_units: null,
        total_units: null,
      },
      attention: [],
      completed_today: [],
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <TasksPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  const task = (await screen.findByText('完成三地薪酬复核')).closest('article')!
  fireEvent.click(within(task).getByRole('button', { name: '编辑任务' }))

  fireEvent.change(within(task).getByLabelText('等待对象'), { target: { value: '华东财务' } })
  fireEvent.change(within(task).getByLabelText('截止时间'), { target: { value: '2026-09-04T10:00' } })
  fireEvent.change(within(task).getByLabelText('真实工作总量'), { target: { value: '3' } })
  fireEvent.change(within(task).getByLabelText('负责人'), { target: { value: 'u-2' } })
  fireEvent.click(within(task).getByRole('button', { name: '保存修改' }))

  await waitFor(() => {
    const patch = fetchMock.mock.calls.find(
      ([input, init]) => String(input).includes('/tasks/task-9') && init?.method === 'PATCH',
    )
    expect(patch).toBeDefined()
    const body = JSON.parse(String(patch![1]?.body))
    expect(body.waiting_for).toBe('华东财务')
    expect(body.due_at).toContain('2026-09-04')
    expect(body.total_units).toBe(3)
    expect(body.owner_user_id).toBe('u-2')
    expect(body.title).toBe('完成三地薪酬复核')
  })
})

test('splits a subtask with name, next action, owner and deadline', async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/api/work-summaries/assignable-owners')) {
      return Response.json({ owners: [{ user_id: 'u-1', name: '王经理' }, { user_id: 'u-2', name: '李经理' }] })
    }
    if (url.endsWith('/api/work-summaries/tasks/task-9/subtasks') && init?.method === 'POST') {
      return Response.json({ task_id: 'sub-1' }, { status: 201 })
    }
    return Response.json({
      continue_work: {
        work_id: 'task-9',
        work_type: 'work_task',
        title: '三地薪酬复核',
        business_status: '处理中',
        next_action: '拆分地区任务',
        resume_target: '/tasks',
        updated_at: '2026-08-31T08:00:00Z',
        due_at: null,
        owner: null,
        waiting_for: null,
        progress_mode: 'stage',
        completed_units: null,
        total_units: null,
      },
      attention: [],
      completed_today: [],
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <TasksPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  const task = (await screen.findByText('三地薪酬复核')).closest('article')!
  fireEvent.click(within(task).getByRole('button', { name: '拆分任务' }))
  fireEvent.change(within(task).getByLabelText('子任务名称'), { target: { value: '完成华东复核' } })
  fireEvent.change(within(task).getByLabelText('子任务下一步'), { target: { value: '核对差异并留痕' } })
  fireEvent.change(within(task).getByLabelText('子任务负责人'), { target: { value: 'u-2' } })
  fireEvent.change(within(task).getByLabelText('子任务截止时间'), { target: { value: '2026-09-02T17:00' } })
  fireEvent.click(within(task).getByRole('button', { name: '创建子任务' }))

  await waitFor(() => {
    const split = fetchMock.mock.calls.find(
      ([input, init]) => String(input).endsWith('/tasks/task-9/subtasks') && init?.method === 'POST',
    )
    expect(split).toBeDefined()
    const body = JSON.parse(String(split![1]?.body))
    expect(body.title).toBe('完成华东复核')
    expect(body.next_action).toBe('核对差异并留痕')
    expect(body.owner_user_id).toBe('u-2')
    expect(body.due_at).toContain('2026-09-02')
  })
})

test('surfaces the real API error when saving fails', async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/api/work-summaries/assignable-owners')) {
      return Response.json({ owners: [] })
    }
    if (url.includes('/api/work-summaries/tasks/task-9/advance') && init?.method === 'POST') {
      return Response.json({ message: '任务已完成全部单位' }, { status: 409 })
    }
    return Response.json({
      continue_work: {
        work_id: 'task-9',
        work_type: 'work_task',
        title: '完成三地薪酬复核',
        business_status: '处理中',
        next_action: '先核对华东数据',
        resume_target: '/tasks',
        updated_at: '2026-08-31T08:00:00Z',
        due_at: null,
        owner: null,
        waiting_for: null,
        progress_mode: 'units',
        completed_units: 2,
        total_units: 3,
      },
      attention: [],
      completed_today: [],
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <TasksPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  const task = (await screen.findByText('完成三地薪酬复核')).closest('article')!
  fireEvent.click(within(task).getByRole('button', { name: '完成一个单位' }))

  expect(await screen.findByText(/任务已完成全部单位/)).toBeVisible()
})

test('renders several work_task rows without collapsing them by work_type', async () => {
  const mk = (id: string, title: string) => ({
    work_id: id,
    work_type: 'work_task',
    title,
    business_status: '处理中',
    next_action: '继续',
    resume_target: '/tasks',
    updated_at: '2026-08-31T08:00:00Z',
    due_at: null,
    owner: null,
    waiting_for: null,
    progress_mode: 'stage',
    completed_units: null,
    total_units: null,
  })
  renderPage({
    continue_work: mk('task-a', '薪酬复核'),
    attention: [mk('task-b', '面谈安排')],
    completed_today: [],
  })

  expect(await screen.findByText('薪酬复核')).toBeVisible()
  expect(screen.getByText('面谈安排')).toBeVisible()
})