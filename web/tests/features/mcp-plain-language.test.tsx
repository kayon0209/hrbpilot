/**
 * MCP 外部工具页 —— 普通用户可读性回归（2026-09-13）。
 *
 * 锁六件事：
 * 1. 开发者词汇退场：不再出现「只读工具 / 写工具 / 试调」这类叫法；
 * 2. 工具卡不再直出 input_schema 大段 JSON，也不暴露内部英文工具名（A5）；
 * 3. 每个工具给出中文名 + 「可以这样对 AI 说」的示例说法；
 * 4. 参数默认用中文表单填写，JSON 退到折叠的「高级模式」但仍可用（A6）；
 * 5. 在线体验返回办理类结果时先给一句人话结论（含审批去向），原始 JSON 收进折叠区；
 * 6. 角色权限收窄时页面要解释「为什么我比别人少几个」（A2）。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, test, vi } from 'vitest'
import { McpPage } from '../../src/features/mcp/McpPage'

const CAPABILITIES = {
  server: 'hrbpilot-mcp',
  scope: 'L',
  role: 'hrbp',
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
      // 哨兵字段挂在 schema 顶层（不是 properties 里）：表单只读 properties，
      // 所以只要它出现，就说明整份 schema 又被原样铺到界面上了。
      input_schema: {
        type: 'object',
        properties: { query: { type: 'string' }, top_k: { type: 'integer', default: 3 } },
        required: ['query'],
        sentinelSchemaMarker: 'never-render-me',
      },
      requires_approval: false,
      capability: 'policy_qa',
    },
    {
      name: 'get_policy_source',
      kind: 'read',
      description: null,
      input_schema: { type: 'object', properties: { document_name: { type: 'string' } }, required: ['document_name'] },
      requires_approval: false,
      capability: 'policy_qa',
    },
  ],
  write_tools: [
    {
      name: 'create_hr_case',
      kind: 'write',
      description: null,
      input_schema: {
        type: 'object',
        properties: {
          title: { type: 'string' },
          risk_level: { type: 'string', pattern: '^(LOW|MEDIUM|HIGH)$', default: 'LOW' },
          description: { anyOf: [{ type: 'string' }, { type: 'null' }] },
          params: { type: 'object' },
        },
        required: ['title'],
      },
      requires_approval: true,
      capability: 'hr_case',
    },
  ],
  hidden_tool_count: 0,
  hidden_reason: null,
  write_mode: 'create-approval-request',
  auth: '写需登录',
}

function renderPage(onCall?: (body: string) => object, overrides: Record<string, unknown> = {}) {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/api/mcp/capabilities')) return Response.json({ ...CAPABILITIES, ...overrides })
      if (url.includes('/call')) {
        calls.push(String(init?.body ?? ''))
        return Response.json(onCall?.(String(init?.body ?? '')) ?? { ok: true, tool: 'search_policy' })
      }
      return Response.json({})
    }),
  )
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <McpPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return { calls, ...view }
}

function openWriteTool() {
  fireEvent.change(screen.getByLabelText('选择工具'), { target: { value: 'create_hr_case' } })
}

/**
 * 等到工具目录渲染完成。
 *
 * 刻意不用 `findByText('查询制度')`：这个词现在同时是工具卡标题和下拉选项
 * （这正是收敛掉英文工具名之后的结果），匹配它会撞上「多元素」。
 */
function ready() {
  return screen.findByText('查询类 · 只查不改')
}

/**
 * 按说明文字定位到对应的工具卡。
 *
 * 同一句说明在工具卡和试调区各出现一次（试调区会重复所选工具的说明），
 * 所以不能直接 getByText，要挑出属于 `<article>` 卡片的那一份。
 */
function cardWith(text: string): HTMLElement {
  const card = screen
    .getAllByText(text)
    .map(node => node.closest('article'))
    .find(Boolean)
  if (!card) throw new Error(`没有找到包含「${text}」的工具卡`)
  return card
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

  await ready()

  const readCard = cardWith('在制度资料里按关键词找出相关条款，并告诉你结果出自哪份文件。')
  expect(within(readCard).getByText('查询制度')).toBeVisible()
  expect(within(readCard).getByText(/请假超过三天需要哪些审批/)).toBeVisible()

  const writeCard = cardWith('登记一条新的员工事务，例如试用期异常、离职面谈、投诉受理。')
  expect(within(writeCard).getByText('新建 HR 案件')).toBeVisible()
  expect(within(writeCard).getByText('提交后需 HR 批准才生效')).toBeVisible()
})

test('does not dump the raw input schema on tool cards', async () => {
  renderPage()

  await ready()
  expect(screen.queryByText(/sentinelSchemaMarker/)).toBeNull()
  expect(screen.queryByText(/never-render-me/)).toBeNull()
})

test('never shows the internal English tool names to end users', async () => {
  renderPage()

  await ready()

  // 工具名（search_policy / create_hr_case …）只在接口与日志里出现，
  // 界面上从卡片标题、下拉选项到参数区一律用中文名。
  expect(screen.queryByText('search_policy')).toBeNull()
  expect(screen.queryByText(/search_policy/)).toBeNull()
  expect(screen.queryByText(/create_hr_case/)).toBeNull()
  expect(screen.queryByText('hrbpilot_ping')).toBeNull()

  // 下拉里也不带英文：选项文本必须是纯中文。
  const select = screen.getByLabelText('选择工具') as HTMLSelectElement
  const optionTexts = Array.from(select.options).map(option => option.textContent ?? '')
  expect(optionTexts).toContain('查询制度')
  expect(optionTexts.some(text => /[a-z_]{3,}/.test(text))).toBe(false)
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

test('renders the parameters as labelled form fields instead of a JSON box by default', async () => {
  renderPage()

  await ready()

  // 中文业务标签 + 必填标记，替代原来的「参数怎么填」说明列表。
  const query = screen.getByLabelText('你要问的问题')
  expect(query).toBeVisible()
  expect(screen.getByLabelText('返回几条结果（1–10）')).toHaveValue(3)

  // JSON 仍然在，但已经收进默认折叠的「高级模式」。
  const advanced = screen.getByText('高级模式：直接编辑参数').closest('details')!
  expect(advanced.open).toBe(false)
  expect(within(advanced).getByLabelText('参数')).toBeInTheDocument()

  expect(screen.getByRole('button', { name: '开始查询' })).toBeVisible()
})

test('submits exactly what the form holds, omitting blanks so server defaults apply', async () => {
  const { calls } = renderPage()

  await ready()
  fireEvent.change(screen.getByLabelText('你要问的问题'), { target: { value: '年假怎么算' } })
  fireEvent.change(screen.getByLabelText('返回几条结果（1–10）'), { target: { value: '5' } })
  fireEvent.click(screen.getByRole('button', { name: '开始查询' }))

  await waitFor(() => expect(calls).toHaveLength(1))
  const body = JSON.parse(calls[0]) as { arguments: Record<string, unknown> }
  // 数字字段保持数字类型（字符串 "5" 会被后端拒掉），没填的字段干脆不发。
  expect(body.arguments).toEqual({ query: '年假怎么算', top_k: 5 })
})

test('switching to a write tool explains the approval step before submitting', async () => {
  renderPage()

  await ready()
  openWriteTool()

  expect(screen.getByRole('button', { name: '提交办理' })).toBeVisible()
  expect(screen.getAllByText('必填').length).toBeGreaterThanOrEqual(2)
  expect(screen.getByLabelText(/案件编号/)).toBeRequired()
  expect(screen.getByLabelText(/一句话说明这件事/)).toBeRequired()

  // 紧急程度这类固定取值给出中文选项，并且默认值与服务端一致。
  expect(screen.getByLabelText('紧急程度')).toHaveValue('LOW')

  // 通知类工具里表单装不下的字段要被明确指出来，而不是被悄悄丢掉。
  expect(screen.getByText(/表单装不下的内容（params）/)).toBeVisible()
})

test('refuses to submit a write tool while the case id is still the placeholder', async () => {
  const { calls } = renderPage()

  await ready()
  openWriteTool()
  fireEvent.change(screen.getByLabelText(/一句话说明这件事/), { target: { value: '试用期异常跟进' } })
  fireEvent.click(screen.getByRole('button', { name: '提交办理' }))

  // 不把后端那句中英混杂的 "HR case not found" 抛给用户，也不真的发请求。
  expect(await screen.findByText(/办理类必须先有案件编号/)).toBeVisible()
  expect(screen.queryByText(/HR case not found/)).toBeNull()
  expect(calls).toHaveLength(0)
})

test('blocks submission and says so when the advanced JSON is malformed', async () => {
  renderPage()

  await ready()
  fireEvent.change(screen.getByLabelText('参数'), { target: { value: '{ "query": ' } })

  // 折叠起来的 details 在 jsdom 里不算「可见」，但它必须在文档里。
  expect(await screen.findByText(/参数内容不完整/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '开始查询' })).toBeDisabled()
})

test('keeps raw JSON editing available for fields the form cannot express', async () => {
  const { calls } = renderPage()

  await ready()
  openWriteTool()
  fireEvent.change(screen.getByLabelText(/案件编号/), { target: { value: 'case-9' } })
  fireEvent.change(screen.getByLabelText(/一句话说明这件事/), { target: { value: '试用期异常跟进' } })
  // 表单里没有 params，只能在高级模式里补。
  const args = screen.getByLabelText('参数') as HTMLTextAreaElement
  fireEvent.change(args, {
    target: { value: JSON.stringify({ case_id: 'case-9', title: '试用期异常跟进', params: { note: 'hi' } }) },
  })
  fireEvent.click(screen.getByRole('button', { name: '提交办理' }))

  await waitFor(() => expect(calls).toHaveLength(1))
  const body = JSON.parse(calls[0]) as { arguments: Record<string, unknown> }
  expect(body.arguments).toEqual({ case_id: 'case-9', title: '试用期异常跟进', params: { note: 'hi' } })
})

test('summarises an approval result in plain words and hides the raw payload', async () => {
  const { calls } = renderPage(() => ({
    ok: true,
    tool: 'create_hr_case',
    outcome: 'AWAITING_APPROVAL',
    user_message:
      '已提交（编号 AP-77），等待 HR 批准后才会真正执行。同一案件上参数相同的请求只会保留一条待审批记录，重复提交不会重复建单。',
    approval_id: 'AP-77',
    status: 'AWAITING_APPROVAL',
    tenant_id: 'tenant-demo',
  }))

  await ready()
  openWriteTool()
  fireEvent.change(screen.getByLabelText(/案件编号/), { target: { value: 'case-9' } })
  fireEvent.change(screen.getByLabelText(/一句话说明这件事/), { target: { value: '试用期异常跟进' } })
  fireEvent.click(screen.getByRole('button', { name: '提交办理' }))
  await waitFor(() => expect(calls).toHaveLength(1))

  // 文案来自后端契约，前端不再自己猜"这算成功还是失败"。
  await waitFor(() => expect(screen.getAllByText(/已提交（编号 AP-77）/).length).toBeGreaterThan(0))

  // 结论在折叠区之外（原始 JSON 里也会出现同样的文案，所以要先把它排除）。
  const fold = screen.getByText('查看原始返回数据（技术同学用）').closest('details')!
  const headline = screen.getAllByText(/已提交（编号 AP-77）/).filter(node => !fold.contains(node))
  expect(headline).toHaveLength(1)
  expect(headline[0]).toBeVisible()
  expect(headline[0]).toHaveTextContent(/重复提交不会重复建单/)

  expect(fold.open).toBe(false)
  expect(fold).toHaveTextContent(/AWAITING_APPROVAL/)
})

test('explains failures with the backend user message rather than a raw error code', async () => {
  renderPage(() => ({
    ok: false,
    tool: 'search_policy',
    outcome: 'FAILED',
    error_code: 'RETRIEVAL_UNAVAILABLE',
    user_message: '制度检索服务暂时不可用，请稍后重试。本次没有返回任何制度内容，不会用推测补上。',
  }))

  await ready()
  fireEvent.click(screen.getByRole('button', { name: '开始查询' }))

  const fold = (await screen.findByText('查看原始返回数据（技术同学用）')).closest('details')!
  const headline = screen.getAllByText(/制度检索服务暂时不可用/).filter(node => !fold.contains(node))
  expect(headline).toHaveLength(1)
  expect(headline[0]).toBeVisible()
  // 内部错误码只出现在原始数据里，不进用户可见的那句话。
  expect(headline[0]).not.toHaveTextContent(/RETRIEVAL_UNAVAILABLE/)
})

test('says how many tools are hidden when the current role is narrower', async () => {
  renderPage(undefined, { hidden_tool_count: 5, hidden_reason: '当前角色不持有这项能力所需的业务权限' })

  await ready()
  expect(screen.getByText(/另有 5 个工具因权限范围不同未列出/)).toBeVisible()
})

test('does not explain hidden tools when nothing is hidden', async () => {
  renderPage()

  await ready()
  expect(screen.queryByText(/未列出/)).toBeNull()
})
