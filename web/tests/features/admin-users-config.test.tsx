import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { AdminUsersPage } from '../../src/features/admin-users/AdminUsersPage'

type UserView = {
  user_id: string
  name: string
  email: string
  role: 'employee' | 'hrbp' | 'hr_manager' | 'admin'
  org_unit_id: string | null
  org_unit: string | null
  manager_scope_org_unit_ids: string[]
}

type OrgView = { org_unit_id: string; name: string; parent_id: string | null }
type LegacyWorkView = { work_id: string; work_type: 'async_task' | 'weekly_report'; title: string }

function installApi(users: UserView[], initialOrgs: OrgView[], initialLegacyWork: LegacyWorkView[] = []) {
  let orgs = [...initialOrgs]
  let legacyWork = [...initialLegacyWork]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    const method = init?.method ?? 'GET'
    if (path === '/api/admin/users' && method === 'GET') {
      return Response.json({ users, org_units: orgs })
    }
    if (path === '/api/admin/users/legacy-work' && method === 'GET') {
      return Response.json({ items: legacyWork, total: legacyWork.length })
    }
    if (path === '/api/admin/users/org-units' && method === 'POST') {
      const body = JSON.parse(String(init?.body)) as { name: string }
      const created = { org_unit_id: `org-${orgs.length + 1}`, name: body.name, parent_id: null }
      orgs = [...orgs, created]
      return Response.json(created)
    }
    const assignMatch = path.match(/^\/api\/admin\/users\/([^/]+)\/org-unit$/)
    if (assignMatch && method === 'PUT') {
      const body = JSON.parse(String(init?.body)) as { org_unit_id: string | null }
      const user = users.find(item => item.user_id === assignMatch[1])!
      const org = orgs.find(item => item.org_unit_id === body.org_unit_id)
      user.org_unit_id = body.org_unit_id
      user.org_unit = org?.name ?? null
      return Response.json({ user_id: user.user_id, org_unit_id: body.org_unit_id, org_unit: user.org_unit })
    }
    const scopeMatch = path.match(/^\/api\/admin\/users\/([^/]+)\/manager-scopes$/)
    if (scopeMatch && method === 'PUT') {
      const body = JSON.parse(String(init?.body)) as { org_unit_ids: string[] }
      const manager = users.find(item => item.user_id === scopeMatch[1])!
      manager.manager_scope_org_unit_ids = body.org_unit_ids
      return Response.json({ manager_id: manager.user_id, org_unit_ids: body.org_unit_ids })
    }
    const claimMatch = path.match(/^\/api\/admin\/users\/legacy-work\/([^/]+)\/([^/]+)\/owner$/)
    if (claimMatch && method === 'PUT') {
      const body = JSON.parse(String(init?.body)) as { user_id: string }
      legacyWork = legacyWork.filter(item => item.work_id !== claimMatch[2])
      return Response.json({ work_id: claimMatch[2], work_type: claimMatch[1], owner_user_id: body.user_id })
    }
    return Response.json({ message: `Unexpected ${method} ${path}` }, { status: 500 })
  })
  vi.stubGlobal('fetch', fetchMock)
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><AdminUsersPage /></QueryClientProvider>)
}

const employee: UserView = {
  user_id: 'employee-1',
  name: 'Employee',
  email: 'employee@example.test',
  role: 'employee',
  org_unit_id: null,
  org_unit: null,
  manager_scope_org_unit_ids: [],
}

const manager: UserView = {
  user_id: 'manager-1',
  name: 'Manager',
  email: 'manager@example.test',
  role: 'hr_manager',
  org_unit_id: null,
  org_unit: null,
  manager_scope_org_unit_ids: [],
}

const orgA: OrgView = { org_unit_id: 'org-a', name: '华东事业部', parent_id: null }
const orgB: OrgView = { org_unit_id: 'org-b', name: '华南事业部', parent_id: null }

afterEach(() => vi.unstubAllGlobals())

describe('admin organisation configuration', () => {
  test('creates an organisation and refreshes the visible inventory', async () => {
    installApi([{ ...employee }], [])
    renderPage()
    const user = userEvent.setup()

    await user.type(await screen.findByLabelText('组织名称'), '华东事业部')
    await user.click(screen.getByRole('button', { name: '创建组织' }))

    expect(await screen.findByText('组织已创建：华东事业部')).toBeInTheDocument()
    expect(await screen.findByRole('option', { name: '华东事业部' })).toBeInTheDocument()
  })

  test('assigns a user to an organisation and shows the persisted result', async () => {
    installApi([{ ...employee }], [orgA])
    renderPage()
    const user = userEvent.setup()

    await user.selectOptions(await screen.findByLabelText('Employee 的组织'), 'org-a')

    const card = screen.getByRole('article', { name: 'Employee' })
    expect(await within(card).findByText('员工 · 华东事业部')).toBeInTheDocument()
  })

  test('replaces a manager scope and shows the authorised organisations', async () => {
    installApi([{ ...manager }], [orgA, orgB])
    renderPage()
    const user = userEvent.setup()

    await user.click(await screen.findByLabelText('Manager 授权 华东事业部'))
    await user.click(screen.getByLabelText('Manager 授权 华南事业部'))
    await user.click(screen.getByRole('button', { name: '保存 Manager 的经理范围' }))

    expect(await screen.findByText('经理范围：华东事业部、华南事业部')).toBeInTheDocument()
  })

  test('claims ownerless migrated work for an explicit HR owner', async () => {
    installApi(
      [{ ...manager }],
      [],
      [{ work_id: 'legacy-1', work_type: 'async_task', title: '面谈纪要分析' }],
    )
    renderPage()
    const user = userEvent.setup()

    await user.selectOptions(await screen.findByLabelText('面谈纪要分析 的负责人'), 'manager-1')
    await user.click(screen.getByRole('button', { name: '认领 面谈纪要分析' }))

    expect(await screen.findByText('历史工作已认领：面谈纪要分析')).toBeInTheDocument()
    expect(screen.queryByLabelText('面谈纪要分析 的负责人')).not.toBeInTheDocument()
  })
})
