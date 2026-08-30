import { z } from 'zod'

const errorSchema = z.object({
  code: z.string().optional(),
  message: z.string().optional(),
  detail: z.union([z.string(), z.object({ message: z.string().optional() })]).optional(),
  request_id: z.string().optional(),
}).passthrough()

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
    public requestId?: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

type ClientOptions = {
  getAccessToken?: () => string | null
  refresh?: () => Promise<string | null>
  onUnauthorized?: () => void
}

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

export class ApiClient {
  constructor(private readonly options: ClientOptions = {}) {}

  async request<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
    const headers = new Headers(init.headers)
    const token = this.options.getAccessToken?.()
    if (token) headers.set('Authorization', `Bearer ${token}`)
    if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json')
    }

    const response = await fetch(`${API_BASE}${path}`, { ...init, headers })
    if (response.status === 401 && retry && this.options.refresh) {
      const refreshed = await this.options.refresh()
      if (refreshed) return this.request<T>(path, init, false)
      this.options.onUnauthorized?.()
    }
    if (!response.ok) throw await normalizeError(response)
    if (response.status === 204) return undefined as T
    const contentType = response.headers.get('content-type') ?? ''
    return (contentType.includes('json') ? response.json() : response.text()) as Promise<T>
  }
}

export async function normalizeError(response: Response): Promise<ApiError> {
  let body: unknown
  try { body = await response.json() } catch { body = undefined }
  const parsed = errorSchema.safeParse(body)
  const value = parsed.success ? parsed.data : {}
  const detail = typeof value.detail === 'string' ? value.detail : value.detail?.message
  return new ApiError(response.status, value.message ?? detail ?? `请求失败（${response.status}）`, value.code, value.request_id)
}

let handlers: Required<ClientOptions> = {
  getAccessToken: () => null,
  refresh: async () => null,
  onUnauthorized: () => undefined,
}

export function configureApiClient(next: Partial<Required<ClientOptions>>) {
  handlers = { ...handlers, ...next }
}

export const apiClient = new ApiClient({
  getAccessToken: () => handlers.getAccessToken(),
  refresh: () => handlers.refresh(),
  onUnauthorized: () => handlers.onUnauthorized(),
})

export const publicClient = new ApiClient()

export function authHeaders(init?: HeadersInit) {
  const headers = new Headers(init)
  const token = handlers.getAccessToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  return headers
}

/**
 * fetch with auth + 401→refresh→retry-once, for streaming endpoints that
 * can't go through ApiClient.request (they need the raw Response body).
 */
export async function authorizedFetch(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers: authHeaders(init.headers) })
  if (response.status === 401 && retry) {
    const refreshed = await handlers.refresh()
    if (refreshed) return authorizedFetch(path, init, false)
    handlers.onUnauthorized()
  }
  return response
}
