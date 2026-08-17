import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, test, vi } from 'vitest'
import { App } from '../../src/app/App'
import { useSessionStore } from '../../src/app/session-store'

afterEach(() => {
  useSessionStore.getState().logout()
  vi.restoreAllMocks()
})

test('submits email and password then opens the overview', async () => {
  vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(new Response(JSON.stringify({ access_token: 'a', refresh_token: 'r', expires_in: 3600 }), { status: 200, headers: { 'content-type': 'application/json' } }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ id: 'u1', email: 'hr@example.com', name: 'HR', role: 'hrbp', tenant_id: 't1' }), { status: 200, headers: { 'content-type': 'application/json' } }))
    .mockResolvedValue(new Response(JSON.stringify({ status: 'ok', checks: {} }), { status: 200, headers: { 'content-type': 'application/json' } }))

  render(<App initialEntries={['/login']} />)
  await userEvent.type(await screen.findByLabelText('邮箱'), 'hr@example.com')
  await userEvent.type(screen.getByLabelText('密码'), 'secret')
  await userEvent.click(screen.getByRole('button', { name: '登录' }))
  expect(await screen.findByRole('heading', { name: '概览' })).toBeVisible()
})
