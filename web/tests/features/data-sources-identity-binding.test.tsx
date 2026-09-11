import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import { DataSourcesPage } from '../../src/features/data-sources/DataSourcesPage'

const intakeSource = {
  source_id: 'source-1',
  name: '员工服务群',
  platform: 'wecom',
  platform_label: '企业微信',
  purpose: '接收员工 HR 事项',
  authorized_scope: 'chat-hr',
  authorized_scope_json: { chat_ids: ['chat-hr'], folder_ids: [] },
  event_route: 'employee_request' as const,
  content_types: ['messages'],
  data_destination: '员工请求',
  certification_level: 4,
  certification_label: '已认证',
  sync_status: 'ok',
  sync_label: '正常',
  last_sync_at: null,
  next_sync_at: null,
  last_error: null,
  paused: false,
  revoked_at: null,
  revoked_reason: null,
  wecom_callback_configured: false,
  wecom_corp_id: null,
  wecom_agent_id: null,
  wecom_callback_path: '/api/connector-webhooks/wecom/tenant-1/source-1',
  updated_at: null,
}

afterEach(() => vi.unstubAllGlobals())

test('administrator explicitly binds a platform account to an employee', async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    const method = init?.method ?? 'GET'
    if (path === '/api/data-sources' && method === 'GET') return Response.json({ sources: [intakeSource] })
    if (path === '/api/admin/users' && method === 'GET') {
      return Response.json({
        users: [
          { user_id: 'employee-1', name: '员工甲', email: 'employee@example.test', role: 'employee' },
          { user_id: 'hrbp-1', name: 'HRBP 乙', email: 'hrbp@example.test', role: 'hrbp' },
        ],
        org_units: [],
      })
    }
    if (path === '/api/data-sources/source-1/identity-bindings' && method === 'POST') {
      expect(JSON.parse(String(init?.body))).toEqual({ external_user_id: 'ou_123', user_id: 'employee-1' })
      return Response.json({ source_id: 'source-1', external_user_id: 'ou_123', user_id: 'employee-1' })
    }
    return Response.json({ message: `Unexpected ${method} ${path}` }, { status: 500 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(<QueryClientProvider client={client}><DataSourcesPage /></QueryClientProvider>)
  const user = userEvent.setup()

  await user.type(await screen.findByLabelText('平台账号 ID'), 'ou_123')
  await user.selectOptions(screen.getByLabelText('HRBPilot 员工'), 'employee-1')
  await user.click(screen.getByRole('button', { name: '确认并绑定' }))

  expect(await screen.findByText('账号已绑定；同一账号此前等待确认的消息将登记为该员工的请求。')).toBeInTheDocument()
  expect(screen.getByRole('option', { name: '员工甲（employee@example.test）' })).toBeInTheDocument()
  expect(screen.queryByRole('option', { name: 'HRBP 乙（hrbp@example.test）' })).not.toBeInTheDocument()
})

test('administrator stores WeCom callback configuration without rendering its secrets', async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    const method = init?.method ?? 'GET'
    if (path === '/api/data-sources' && method === 'GET') return Response.json({ sources: [intakeSource] })
    if (path === '/api/admin/users' && method === 'GET') return Response.json({ users: [], org_units: [] })
    if (path === '/api/data-sources/source-1/wecom-callback-config' && method === 'PUT') {
      expect(JSON.parse(String(init?.body))).toEqual({
        corp_id: 'ww-test-corp', agent_id: '1000002', corp_secret: 'corp-secret',
        callback_token: 'CallbackToken1', encoding_aes_key: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
      })
      return Response.json({
        source_id: 'source-1', configured: true, corp_id: 'ww-test-corp', agent_id: '1000002',
        callback_path: '/api/connector-webhooks/wecom/tenant-1/source-1',
      })
    }
    return Response.json({ message: `Unexpected ${method} ${path}` }, { status: 500 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(<QueryClientProvider client={client}><DataSourcesPage /></QueryClientProvider>)
  const user = userEvent.setup()

  await user.type(await screen.findByLabelText('CorpID'), 'ww-test-corp')
  await user.type(screen.getByLabelText('AgentID'), '1000002')
  await user.type(screen.getByLabelText('Secret'), 'corp-secret')
  await user.type(screen.getByLabelText('回调 Token'), 'CallbackToken1')
  await user.type(screen.getByLabelText('EncodingAESKey'), 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA')
  await user.click(screen.getByRole('button', { name: '保存回调配置' }))

  expect(await screen.findByText('已加密保存回调配置。请在企业微信自建应用中填入本数据源的回调地址并完成 URL 验证。')).toBeInTheDocument()
  expect(screen.getByText('/api/connector-webhooks/wecom/tenant-1/source-1')).toBeInTheDocument()
  expect(screen.getByLabelText('Secret')).toHaveValue('')
  expect(screen.getByLabelText('回调 Token')).toHaveValue('')
  expect(screen.getByLabelText('EncodingAESKey')).toHaveValue('')
})
