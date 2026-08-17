import { afterEach, expect, test, vi } from 'vitest'
import { ApiClient, ApiError } from '../../src/api/http'

afterEach(() => vi.restoreAllMocks())

test('adds the bearer token and normalizes an API error', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
    JSON.stringify({ code: 'AUTH_ERROR', message: 'Missing token', request_id: 'r-1' }),
    { status: 401, headers: { 'content-type': 'application/json' } },
  ))
  const client = new ApiClient({ getAccessToken: () => 'access-token' })
  await expect(client.request('/api/auth/me')).rejects.toMatchObject<ApiError>({ status: 401, code: 'AUTH_ERROR' })
  expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get('Authorization')).toBe('Bearer access-token')
})

test('returns no content without attempting JSON parsing', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 204 }))
  await expect(new ApiClient().request('/api/kb/document')).resolves.toBeUndefined()
})
