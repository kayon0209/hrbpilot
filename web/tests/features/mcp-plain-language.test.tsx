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
  // 权限套餐由后端派生（label + description + scopes）。**说明文案必须来自后端**：
  // 前端曾按 key 名猜（`key.includes('propose')`），那样改了键名就会静默配错说明。
  scope_packages: {
    query_only: {
      label: '仅查询',
      description: '只查制度、案件和审批状态，不会改动任何数据。',
      scopes: ['hrb:policy:read'],
    },
    query_and_propose: {
      label: '查询 + 发起办理申请',
      description: '可以生成办理草稿并提交审批，但不会直接修改案件、任务或通知。',
      scopes: ['hrb:policy:read', 'hrb:case:propose'],
    },
  },
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
  const revoked: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/api/mcp/my-connections')) {
        // 按 overrides 里的 my_installations 推导：有实例就模拟"最近调用成功"（healthy），
        // 没有就是 no_installations —— 让"检查我的接入状态"的两种主路径都可测。
        const installs = Array.isArray(overrides.my_installations) ? overrides.my_installations : []
        const now = new Date().toISOString()
        return Response.json({
          checked_at: now,
          installations: installs.map(item => ({
            ...item,
            status: 'active',
            last_call: { tool: 'search_policy', outcome: 'FOUND', latency_ms: 120, at: now },
            last_failure: null,
            last_call_ok_after_failure: false,
            calls_7d: 3,
          })),
          server: { tool_count: 2, hidden_tool_count: 0 },
          check:
            installs.length > 0
              ? {
                  code: 'healthy',
                  installs: installs.length,
                  live: installs.length,
                  failing: 0,
                  tool_count: 2,
                  latest_call: { tool: 'search_policy', outcome: 'FOUND', latency_ms: 120, at: now },
                }
              : { code: 'no_installations', installs: 0, live: 0, failing: 0, tool_count: 2, latest_call: null },
        })
      }
      if (url.includes('/api/mcp/capabilities')) return Response.json({ ...CAPABILITIES, ...overrides })
      if (url.includes('/my-installations/') && url.includes('/revoke')) {
        revoked.push(url)
        return Response.json({ family_id: 'f-1', revoked: true, user_message: '已解绑。' })
      }
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
  return { calls, revoked, ...view }
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
async function ready() {
  // 等页面主标题：它一定先于任何数据块出现，且不随后端返回的字段增减而变。
  await screen.findByRole('heading', { name: '把你自己的 AI 助手接上' })
  // 联调工具与租户级管理默认收起（产品决定：HR 的视界里不该先出现开发内容）。
  // 绝大多数断言针对折叠里的内容，所以这里统一展开；要验证"默认收起"本身的用例
  // 不调这个辅助函数，自己先断言 open 再展开。
  // 只展开**顶层**折叠：嵌套的「高级模式 / 查看原始返回数据」要保持默认收起 ——
  // 那是它们各自用例要验证的东西，被这里顺带打开就等于那些断言不再生效。
  for (const fold of Array.from(document.querySelectorAll('main > details'))) {
    fold.open = true
  }
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

  // 标题与导语说的是"要做什么"，不是"这是什么技术"。
  expect(await screen.findByRole('heading', { name: '把你自己的 AI 助手接上' })).toBeVisible()
  // 承诺必须与部署现实一致：多租户或公司统一管理 AI 工具时，确实可能需要管理员允许，
  // 所以不再写"不需要找 IT" —— 那是一句会在那些部署里落空的承诺。
  expect(screen.getByText(/完成一次连接后/)).toBeVisible()
  expect(screen.getByText(/连接可以随时断开/)).toBeVisible()
  expect(screen.queryByText(/不需要找 IT/)).toBeNull()

  // 首屏就是一条可照做的三步引导 —— 而不是靠几段话去解释"这一页是什么"。
  expect(screen.getByText('先选权限范围')).toBeVisible()
  expect(screen.getByText('按下面的步骤接上')).toBeVisible()
  expect(screen.getByText('试一句，确认通了')).toBeVisible()

  // 元叙述已删除：用解释文字去补结构不清，只会生产更多需要解释的内容。
  expect(screen.queryByText('三句话看懂这个页面')).toBeNull()
  expect(screen.queryByRole('heading', { name: /两种.没反应.是正常的/ })).toBeNull()

  // 旧称呼一律不再出现在用户可见文案里。
  expect(screen.queryByText('只读工具')).toBeNull()
  expect(screen.queryByText('写工具（仅建审批）')).toBeNull()
  expect(screen.queryByRole('button', { name: '试调' })).toBeNull()

  // 整页不得出现协议名（DESIGN.md §9 禁用词）—— 这是"去开发者术语"的硬判据。
  expect(document.body.textContent ?? '').not.toMatch(/MCP/)

  // 两类工具改成用户能判断的叫法。
  expect(screen.getAllByText('查询类').length).toBeGreaterThan(0)
  expect(screen.getAllByText('办理类').length).toBeGreaterThan(0)
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

test('groups tools under user-facing headings and keeps developer details folded away', async () => {
  renderPage()
  // 这里刻意**不用** ready()（它会展开折叠）：本用例要验证的正是默认收起。
  await screen.findByRole('heading', { name: '把你自己的 AI 助手接上' })

  const devFold = screen.getByText('联调工具（给开发同学）').closest('details')!
  expect(devFold.open).toBe(false)

  // 展开后再断言里面的分组标题。
  devFold.open = true
  expect(screen.getByText('查询类 · 只查不改')).toBeVisible()
  expect(screen.getByText('办理类 · 需 HR 批准')).toBeVisible()
  // 只钉语义（页面上有一份工具清单），不钉具体措辞 —— 该文案还在迭代。
  expect(screen.getByRole('heading', { name: /工具/ })).toBeVisible()

  // 默认没有任何安装实例时，首屏铺的是接入步骤，而不是"已接上"。
  expect(screen.queryByText('你已经有接上的助手')).toBeNull()
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
  const fold = screen.getByText('查看原始返回数据').closest('details')!
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

  const fold = (await screen.findByText('查看原始返回数据')).closest('details')!
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

test('gives administrators a comprehensible connection manager and wires its containment actions', async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/api/mcp/capabilities')) return Response.json({ ...CAPABILITIES, role: 'admin' })
    if (url.includes('/api/admin/mcp/clients')) {
      return Response.json([
        {
          client_id: 'client-demo',
          client_name: '常用办公助手',
          registration_source: 'dcr',
          installations: 1,
          active_installations: 1,
          last_seen_at: null,
          blocked: false,
        },
      ])
    }
    if (url.includes('/api/admin/mcp/installations')) {
      return Response.json([
        {
          family_id: 'family-demo',
          client_id: 'client-demo',
          user_id: 'user-id-only',
          user_name: '李明',
          role: 'hrbp',
          active_refresh_tokens: 1,
          created_at: null,
          last_rotated_at: null,
          revoked_at: null,
        },
      ])
    }
    return Response.json({})
  })
  vi.stubGlobal('fetch', fetchMock)
  vi.stubGlobal('confirm', vi.fn(() => true))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <McpPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  // 管理能力收进「仅管理员可见」的折叠区：普通 HR 的视界里不该出现租户级管理。
  expect(await screen.findByText('租户级管理（仅管理员可见）')).toBeInTheDocument()
  // 租户级管理也是默认收起的顶层折叠，断言里面的内容前先展开。
  const adminFold = screen.getByText('租户级管理（仅管理员可见）').closest('details')!
  adminFold.open = true
  expect(await screen.findAllByText('常用办公助手')).toHaveLength(2)
  expect(screen.getByText(/李明 · hrbp · 有效/)).toBeVisible()

  fireEvent.click(screen.getByRole('button', { name: '封禁并吊销' }))
  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/api/admin/mcp/clients/client-demo/revoke'))).toBe(true),
  )

  fireEvent.click(screen.getByRole('button', { name: '紧急撤销全部连接' }))
  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/api/admin/mcp/revoke-all'))).toBe(true),
  )
})

/* ---------------------------------------------------------------------- */
/* 我接过的助手（用户自助）                                                */
/* ---------------------------------------------------------------------- */

const MINE = [
  {
    family_id: 'f-1',
    client_id: 'codex-cli',
    user_id: 'u-1',
    user_name: '李明',
    role: 'hrbp',
    active_refresh_tokens: 2,
    created_at: null,
    last_rotated_at: null,
    revoked_at: null,
    scopes: ['hrb:policy:read', 'hrb:case:propose'],
  },
  {
    family_id: 'f-2',
    client_id: 'workbuddy-desktop',
    user_id: 'u-1',
    user_name: '李明',
    role: 'hrbp',
    active_refresh_tokens: 1,
    created_at: null,
    last_rotated_at: null,
    revoked_at: null,
    scopes: ['hrb:policy:read'],
  },
]

test('lists the assistants the user has connected, in names the user recognises', async () => {
  renderPage(undefined, { my_installations: MINE })
  await ready()

  // 助手名用用户认得的名字（同时出现在选择按钮与"我接过的"列表里，故用 AllBy）。
  expect(screen.getAllByText('Codex').length).toBeGreaterThan(1)
  expect(screen.getAllByText('WorkBuddy').length).toBeGreaterThan(0)
  // 原始 client_id 不对用户暴露。
  expect(screen.queryByText(/codex-cli/)).toBeNull()

  // 权限范围给的是用户说法，判据是"有没有全权写入位"，不是数 scope 个数。
  // 用「·」锚定到**列表项**：套餐名那一处是同样的字，但后面跟的是说明而不是"· 最近活动"。
  expect(screen.getByText(/查询 \+ 发起办理申请 ·/)).toBeVisible()
  expect(screen.getByText(/仅查询 ·/)).toBeVisible()
})

test('unbinds through the user-facing route, not the admin one', async () => {
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
  try {
    const { revoked } = renderPage(undefined, { my_installations: MINE })
    await ready()

    fireEvent.click(screen.getAllByRole('button', { name: '断开连接' })[0])

    // 解绑不可撤销，而且会连带作废这个助手提交的待审批/已批准未执行的办理
    // （后端 `_cancel_bound_approvals`）—— 所以必须先问一次。
    expect(confirmSpy).toHaveBeenCalled()
    await waitFor(() => expect(revoked).toHaveLength(1))
    // 走用户面接口：管理员面需要 mcp_admin，普通 HR 调不动 ——
    // 而"自己接的能自己解绑"正是这个功能存在的理由。
    expect(revoked[0]).toContain('/api/mcp/my-installations/f-1/revoke')
    expect(revoked[0]).not.toContain('/api/admin/')
  } finally {
    confirmSpy.mockRestore()
  }
})

test('does not unbind at all when the confirmation is declined', async () => {
  // 确认弹窗不能是走个过场：点了"取消"就必须真的什么都不做。
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
  try {
    const { revoked } = renderPage(undefined, { my_installations: MINE })
    await ready()

    fireEvent.click(screen.getAllByRole('button', { name: '断开连接' })[0])

    expect(confirmSpy).toHaveBeenCalled()
    expect(revoked).toHaveLength(0)
  } finally {
    confirmSpy.mockRestore()
  }
})

test('distinguishes "could not read" from "connected nothing"', async () => {
  // 字段缺席 = 这次没查到（后端降级或旧版），不能冒充"你没有" ——
  // 说错了会让用户去重新接一个其实已经接好的助手。
  renderPage(undefined, { my_installations: undefined })
  await ready()
  expect(screen.getByText(/暂时读不到你接过的助手/)).toBeVisible()
})

test('leads with the setup steps when nothing is connected yet', async () => {
  renderPage(undefined, { my_installations: [] })
  await ready()
  // 一个都没接 → 首屏就是"怎么接"，而不是"你已经接上了"。
  expect(screen.getByText('先选权限范围')).toBeVisible()
  expect(screen.queryByText('你已经有接上的助手')).toBeNull()
})

test('leads with what is already connected instead of walking through the steps again', async () => {
  // 这条守的是本轮改版的要点：**已经接过的人再打开这页，不该被引导着又接一遍** ——
  // 每接一遍都会新建一条独立的授权链，列表里就会多出一个同名条目，
  // 用户从此分不清哪条在用，而且解绑时的连带作废范围也随之扩大。
  renderPage(undefined, { my_installations: MINE })
  await ready()

  expect(screen.getByText('你已经有接上的助手')).toBeVisible()
  // 列表渲染出来的直接证据：两条实例各有一个解绑按钮。
  // （不断言助手名 —— "Codex" 在助手选择按钮里也有，那会让这条断言依赖无关内容。）
  expect(screen.getAllByRole('button', { name: '断开连接' })).toHaveLength(2)
  // 三步收进折叠、且默认收起（要再接一个才需要它）。
  const again = screen.getByText('再接一个别的助手').closest('details')!
  expect(again.open).toBe(false)
  // 三步仍然在 DOM 里（在刚才那个折叠内），但收起 → 第一眼看不到，
  // 所以已经接过的人不会被它带着又走一遍流程。
  expect(screen.getByText('先选权限范围')).not.toBeVisible()
})

test('answers "am I connected" from the installation list, not from the web session', async () => {
  // 改版前这个按钮只验网页会话能否调通读工具，助手一个都没接也会报"可以正常使用" ——
  // 那个结论与"我的助手能不能用"无关，却长得像它。
  renderPage(undefined, { my_installations: MINE })
  await ready()

  fireEvent.click(screen.getByRole('button', { name: '检查我的接入状态' }))

  // 结论来自后端的结构化事实（check.code），名字不在句子里 —— 措辞由前端出。
  expect(await screen.findByText(/已接上 2 个，最近一次调用成功/)).toBeVisible()
})

test('says nothing is connected yet, and does not claim the service is fine', async () => {
  renderPage(undefined, { my_installations: [] })
  await ready()

  fireEvent.click(screen.getByRole('button', { name: '检查我的接入状态' }))

  expect(await screen.findByText(/还没有接上任何助手/)).toBeVisible()
  // 「服务端正常」只在确实接上了的时候才有意义：没接上时出现这一项，
  // 用户会把它读成"我的助手可用"。
  expect(screen.queryByText(/服务端是正常的/)).toBeNull()
})

/* ---- 2026-09-15 第二轮：权限说明 / 数据范围 / 兼容性状态 / 复制反馈 ---- */

test('renders the scope description from the backend instead of guessing by key name', async () => {
  renderPage()
  await ready()

  // 说明文案来自 `scope_packages[].description`。按 key 名猜的老写法（`includes('propose')`）
  // 一旦键名改了就会静默配错说明 —— 写着"只查不改"却给了写权限。
  expect(screen.getByText('可以生成办理草稿并提交审批，但不会直接修改案件、任务或通知。')).toBeVisible()
  expect(screen.getByText('查询 + 发起办理申请')).toBeVisible()
})

test('never claims a client is verified while none has been verified end to end', async () => {
  renderPage()
  await ready()

  // 本环境里没有任何客户端走完过授权流程（oauth_clients 为空），所以页面上不能出现"已验证"。
  // 这条守的是"别替尚未验证的兼容性提前承诺"：真的验证过某个助手之后再放开它。
  expect(screen.queryByText(/已验证/)).toBeNull()
  expect(screen.getAllByText(/配置支持/).length).toBeGreaterThan(0)
})

test('states what the assistant can reach and what it cannot', async () => {
  renderPage()
  await ready()

  // 只让用户选"仅查询 / 能办理"是不够的 —— 他还得知道助手能看到什么。
  expect(screen.getByText('允许访问')).toBeVisible()
  expect(screen.getByText('不会发生')).toBeVisible()
  expect(screen.getByText('读不到不属于你的案件')).toBeVisible()
  expect(screen.getByText('助手不能批准申请：批准只在 HRBPilot 内由审批人完成')).toBeVisible()
})

test('reports which command was copied', async () => {
  const original = navigator.clipboard
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: async () => undefined },
    configurable: true,
  })
  try {
    renderPage()
    await ready()
    fireEvent.click(screen.getAllByRole('button', { name: '复制' })[0])
    // 每条命令各自回报，而不是所有按钮都说"已复制" —— 用户要知道**哪一条**进了剪贴板。
    expect(await screen.findByRole('button', { name: '命令已复制' })).toBeVisible()
  } finally {
    Object.defineProperty(navigator, 'clipboard', { value: original, configurable: true })
  }
})

test('says so instead of pretending a failed copy succeeded', async () => {
  // jsdom 里没有 navigator.clipboard，正好就是"剪贴板不可用"这条真实分支。
  // 假装成功会让用户把旧内容粘进终端，而这是整条流程最关键的一次复制。
  Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })
  renderPage()
  await ready()

  fireEvent.click(screen.getAllByRole('button', { name: '复制' })[0])

  expect(await screen.findByRole('button', { name: '请手动选中复制' })).toBeVisible()
})
