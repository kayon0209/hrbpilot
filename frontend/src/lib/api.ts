/** HRBP AI Workbench — Typed fetch API client.

Features:
  - Auto-attach JWT access token from authStore
  - 401 → auto-refresh → retry
  - Typed responses
  - Consistent error handling
*/

import { authStore } from '@/stores/authStore';

const BASE_URL = import.meta.env.VITE_API_URL || '';

export class ApiError extends Error {
  status: number;
  body: Record<string, unknown>;

  constructor(status: number, body: Record<string, unknown>) {
    super(body?.message as string || `API error ${status}`);
    this.status = status;
    this.body = body;
  }
}

async function rawApi<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = authStore.getState().accessToken;

  const isFormData = options.body instanceof FormData;

  const headers: Record<string, string> = {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...options.headers as Record<string, string>,
  };

  // Don't set Content-Type for FormData — browser sets it with boundary
  if (!isFormData) {
    headers['Content-Type'] = 'application/json';
  }

  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers,
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({ message: 'Unknown error' }));
    throw new ApiError(res.status, body);
  }

  if (res.status === 204) return undefined as T;
  return res.json();
}

/** Refresh flow: on 401, try to refresh, then retry original request */
async function apiWithRefresh<T>(path: string, options: RequestInit = {}): Promise<T> {
  try {
    return await rawApi<T>(path, options);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      const refreshed = await authStore.getState().refreshAccessToken();
      if (refreshed) {
        return await rawApi<T>(path, options);
      }
      authStore.getState().logout();
      throw err;
    }
    throw err;
  }
}

export const apiClient = {
  get: <T>(path: string) => apiWithRefresh<T>(path),
  post: <T>(path: string, data: unknown) => {
    if (data instanceof FormData) {
      return apiWithRefresh<T>(path, { method: 'POST', body: data });
    }
    return apiWithRefresh<T>(path, { method: 'POST', body: JSON.stringify(data) });
  },
  put: <T>(path: string, data: unknown) =>
    apiWithRefresh<T>(path, { method: 'PUT', body: JSON.stringify(data) }),
  patch: <T>(path: string, data: unknown) =>
    apiWithRefresh<T>(path, { method: 'PATCH', body: JSON.stringify(data) }),
  delete: <T>(path: string) =>
    apiWithRefresh<T>(path, { method: 'DELETE' }),
};

/** Backward-compatible alias — pages import { api } from '@/lib/api' */
export const api = apiClient;

/** SSE stream helper for policy QA (POST-based streaming) */
export function createSSEConnection(url: string, body: unknown) {
  const token = authStore.getState().accessToken;
  return fetch(`${BASE_URL}${url}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  });
}
