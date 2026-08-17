import { z } from 'zod'
import { apiClient, publicClient } from './http'
import type { TokenPair, UserProfile } from './types'

const tokensSchema = z.object({ access_token: z.string(), refresh_token: z.string(), expires_in: z.number() })
const userSchema = z.object({ id: z.string(), email: z.string(), name: z.string(), role: z.string(), tenant_id: z.string() })

export async function login(email: string, password: string): Promise<TokenPair> {
  return tokensSchema.parse(await publicClient.request('/api/auth/login', {
    method: 'POST', body: JSON.stringify({ email, password }),
  }))
}

export async function refresh(refreshToken: string): Promise<TokenPair> {
  return tokensSchema.parse(await publicClient.request('/api/auth/refresh', {
    method: 'POST', body: JSON.stringify({ refresh_token: refreshToken }),
  }))
}

export async function getMe(): Promise<UserProfile> {
  return userSchema.parse(await apiClient.request('/api/auth/me'))
}
