import { create } from 'zustand'
import * as auth from '../api/auth'
import { configureApiClient } from '../api/http'
import type { UserProfile } from '../api/types'

const REFRESH_KEY = 'hrbpilot.refresh'

interface SessionState {
  accessToken: string | null
  user: UserProfile | null
  pending: boolean
  error: string | null
  login: (email: string, password: string) => Promise<void>
  restore: () => Promise<void>
  logout: () => void
}

function storedRefresh() {
  try { return sessionStorage.getItem(REFRESH_KEY) } catch { return null }
}

export const useSessionStore = create<SessionState>((set, get) => ({
  accessToken: null,
  user: null,
  pending: false,
  error: null,
  async login(email, password) {
    set({ pending: true, error: null })
    try {
      const tokens = await auth.login(email, password)
      sessionStorage.setItem(REFRESH_KEY, tokens.refresh_token)
      set({ accessToken: tokens.access_token })
      const user = await auth.getMe()
      set({ user, pending: false })
    } catch (error) {
      get().logout()
      set({ pending: false, error: error instanceof Error ? error.message : '登录失败' })
      throw error
    }
  },
  async restore() {
    const token = storedRefresh()
    if (!token || get().user) return
    set({ pending: true })
    try {
      const tokens = await auth.refresh(token)
      sessionStorage.setItem(REFRESH_KEY, tokens.refresh_token)
      set({ accessToken: tokens.access_token })
      set({ user: await auth.getMe(), pending: false })
    } catch { get().logout() }
  },
  logout() {
    try { sessionStorage.removeItem(REFRESH_KEY) } catch { /* storage may be unavailable */ }
    set({ accessToken: null, user: null, pending: false, error: null })
  },
}))

configureApiClient({
  getAccessToken: () => useSessionStore.getState().accessToken,
  refresh: async () => {
    const token = storedRefresh()
    if (!token) return null
    try {
      const tokens = await auth.refresh(token)
      sessionStorage.setItem(REFRESH_KEY, tokens.refresh_token)
      useSessionStore.setState({ accessToken: tokens.access_token })
      return tokens.access_token
    } catch { useSessionStore.getState().logout(); return null }
  },
  onUnauthorized: () => useSessionStore.getState().logout(),
})
