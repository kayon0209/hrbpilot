/** HRBP AI Workbench — Auth store (Zustand).

Manages: accessToken, refreshToken, user profile, role, tenant_id.
Provides: login, logout, refreshToken, role checks.
*/

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { apiClient } from '@/lib/api';
import { canAccess, Role } from '@/lib/constants';

interface UserProfile {
  id: string;
  email: string;
  name: string;
  role: Role;
  tenant_id: string;
}

interface AuthState {
  accessToken: string | null;
  refreshToken: string | null;
  user: UserProfile | null;

  // Actions
  login: (email: string, password: string) => Promise<boolean>;
  logout: () => void;
  refreshAccessToken: () => Promise<boolean>;
  fetchProfile: () => Promise<void>;

  // Computed helpers
  isAuthenticated: () => boolean;
  canAccessPage: (requiredRole: Role) => boolean;
}

export const authStore = create<AuthState>()(
  persist(
    (set, get) => ({
      accessToken: null,
      refreshToken: null,
      user: null,

      login: async (email: string, password: string) => {
        try {
          const res = await apiClient.post<{
            access_token: string;
            refresh_token: string;
            expires_in: number;
          }>('/api/auth/login', { email, password });

          set({
            accessToken: res.access_token,
            refreshToken: res.refresh_token,
          });

          await get().fetchProfile();
          return true;
        } catch {
          return false;
        }
      },

      logout: () => {
        set({ accessToken: null, refreshToken: null, user: null });
      },

      refreshAccessToken: async () => {
        const currentRefresh = get().refreshToken;
        if (!currentRefresh) return false;

        try {
          const res = await apiClient.post<{
            access_token: string;
            refresh_token: string;
            expires_in: number;
          }>('/api/auth/refresh', { refresh_token: currentRefresh });

          set({
            accessToken: res.access_token,
            refreshToken: res.refresh_token,
          });
          return true;
        } catch {
          get().logout();
          return false;
        }
      },

      fetchProfile: async () => {
        try {
          const profile = await apiClient.get<UserProfile>('/api/auth/me');
          set({ user: profile });
        } catch {
          // Profile fetch failed, but we might still have a valid token
        }
      },

      isAuthenticated: () => get().accessToken !== null && get().user !== null,

      canAccessPage: (requiredRole: Role) => {
        const user = get().user;
        if (!user) return false;
        return canAccess(user.role, requiredRole);
      },
    }),
    {
      name: 'hrbp-auth',
      partialize: (state) => ({
        accessToken: state.accessToken,
        refreshToken: state.refreshToken,
      }),
    }
  )
);
