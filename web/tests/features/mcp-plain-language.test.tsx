/**
 * MCP 外部工具页 —— 普通用户可读性回归（2026-09-13）。
 *
 * 锁四件事：
 * 1. 开发者词汇退场：不再出现「只读工具 / 写工具 / 试调」这类叫法；
 * 2. 工具卡不再直出 input_schema 大段 JSON（那是接口文档，不是给用户看的）；
 * 3. 每个工具给出中文名 + 「可以这样对 AI 说」的示例说法；
 * 4. 在线体验返回办理类结果时先给一句人话结论（含审批去向），原始 JSON 收进折叠区。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, test, vi } from 'vitest'
import { McpPage } from '../../src/features/mcp/McpPage'

const CAPABILITIES = {
  server: 'hrbpilot-mcp',
  scope: 'L',
  transports: [
    { kind: 'stdio', command: 'python -m app.mcp.server', note: '本地直连' },
    { kind: 'streamable-http', url: '/mcp', note: '远程' },
  ],
  tenant_id: 'tenant-demo',
  read_tools: [
    {
      name: 'search_policy',
      kind: 'read',
      description: null,
      // 哨兵字段：只要它出现在 DOM 里，说明 schema 又被直出了。
      input_schema: { type: 'object', properties: { sentinelSchemaMarker: { type: 'string' } } },
      requires_approval: false,
      capability: 'policy.read',
    },
    {
      name: 'get_policy_source',
      kind: 'read',
      description: null,
      input_schema: { type: 'object' },
      requires_approval: false,
      capability: 'policy.read',
    },
  ],
  write_tools: [
    {
      name: 'create_hr_case',
      kind: 'write',
      description: null,
      input_schema: { type: 'object' },
      requires_approval: true,
      capability: 'hr_case',
    },
  ],
  write_mode: 'create-approval-request',
  auth: '写需登录',
}

function renderPage(onCall?: (body: string) => object) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/api/mcp/capabilities')) return Response.json(CAPABILITIES)
      if (url.includes('/call')) {
        return Response.json(onCall?.(String(init?.body ?? '')) ?? { ok: true, tool: 'search_policy' })
      }
      return Response.json({})
    }),
  )
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <McpPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

afterEach(() => vi.unstubAllGlobals())

test('drops developer jargon and explains what the feature is in plain language', async () => {
  renderPage()

  expect(await screen.findByRole('heading', { name: '这是什么' })).toBeVisible()
  expect(screen.getByText(/MCP 是一种通用的「对接标准」/)).toBeVisible()

  // 旧称呼一律不再出现在用户可见文案里。
  expect(screen.queryByText('只读工具')).toBeNull()
  expect(screen.queryByText('写工具（仅建审批）')).toBeNull()
  expect(screen.queryByRole('button', { name: '试调' })).toBeNull()

  // 两类工具改成用户能判断的叫法。
  expect(screen.getAllByText('查询类').length).toBeGreaterThan(0)
  expect(screen.getAllByText('办理类').length).toBeGreaterThan(0)

  // 与「员工请求」的区分不能丢（原页面有，改写后保留）。
  expect(screen.getByText(/这里不是员工提交申请的入口/)).toBeVisible()
})

test('renders each tool with a plain-language description and an example sentence', async () => {
  renderPage()

  expect(await screen.findByText('查询制度')).toBeVisible()
  expect(screen.getByText('在制度资料里按关键词找出相关条款，并告诉你结果出自哪份文件。')).toBeVisible()
  expect(screen.getAllByText(/请假超过三天需要哪些审批/).length).toBeGreaterThan(0)
  expect(screen.getByText('新建 HR 案件')).toBeVisible()
  expect(screen.getAllByText('提交后需 HR 批准才生效').length).toBeGreaterThan(0)
})

test('does not dump the raw input schema on tool cards', async () => {
  renderPage()

  await screen.findByText('查询制度')
  expect(screen.queryByText(/sentinelSchemaMarker/)).toBeNull()
})

test('groups tools under user-facing headings and keeps the IT config folded away', async () => {
  renderPage()

  expect(await screen.findByText('查询类 · 只查不改')).toBeVisible()
  expect(screen.getByText('办理类 · 需 HR 批准')).toBeVisible()
  expect(screen.getByText('可用工具一览（共 3 个）')).toBeVisible()

  // 接入信息默认折叠：技术字符不以「大段无解释」的形式直接铺在页面上。
  const connect = screen.getByText('查看给 IT / 管理员的接入信息').closest('details')!
  expect(connect.open).toBe(false)
  expect(within(connect).getByText(/-m app\.mcp\.server/)).toBeInTheDocument()
})

test('explains what each parameter means instead of leaving raw field names unexplained', async () => {
  renderPage()

  await screen.findByText('查询制度')
  expect(screen.getByText('你要问的问题，用一句大白话写')).toBeVisible()
  expect(screen.getByText('返回几条结果（1–10）')).toBeVisible()
  expect(screen.getByRole('button', { name: '开始查询' })).toBeVisible()
})

test('switching to a write tool explains the approval step before submitting', async () => {
  renderPage()

  await screen.findByText('查询制度')
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'create_hr_case' } })

  expect(screen.getByRole('button', { name: '提交办理' })).toBeVisible()
  expect(screen.getByText('案件编号，必填')).toBeVisible()
})

test('refuses to submit a write tool while the case id is still the placeholder', async () => {
  renderPage()

  await screen.findByText('查询制度')
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'create_hr_case' } })
  fireEvent.click(screen.getByRole('button', { name: '提交办理' }))

  // 不把后端那句中英混杂的 "HR case not found" 抛给用户。
  expect(await screen.findByText(/办理类必须先有案件编号/)).toBeVisible()
  expect(screen.queryByText(/HR case not found/)).toBeNull()
})

test('summarises an approval result in plain words and hides the raw payload', async () => {
  renderPage(() => ({ ok: true, approval_id: 'AP-77', status: 'AWAITING_APPROVAL', tenant_id: 'tenant-demo' }))

  await screen.findByText('查询制度')
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'create_hr_case' } })
  // pick() 会把参数重置成示例；换成真实案件编号后再提交。
  fireEvent.change(screen.getByLabelText('参数'), {
    target: { value: JSON.stringify({ case_id: 'case-9', title: '试用期异常跟进' }) },
  })
  fireEvent.click(screen.getByRole('button', { name: '提交办理' }))

  await waitFor(() => expect(screen.getByText(/已生成一条待审批记录（编号 AP-77）/)).toBeVisible())
  expect(screen.getByText(/请到「团队待处理」批准后才会真正执行/)).toBeVisible()

  const fold = screen.getByText('查看原始返回数据（技术同学用）').closest('details')!
  expect(fold.open).toBe(false)
  expect(within(fold).getByText(/AWAITING_APPROVAL/)).toBeInTheDocument()
})
