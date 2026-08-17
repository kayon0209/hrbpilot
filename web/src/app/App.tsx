import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { RouterProvider } from 'react-router-dom'
import { createAppRouter } from './router'
import { useSessionStore } from './session-store'
import './App.css'

export function App({ initialEntries }: { initialEntries?: string[] }) {
  const router = useMemo(() => createAppRouter(initialEntries), [initialEntries])
  const client = useMemo(() => new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 5_000 } } }), [])
  return <QueryClientProvider client={client}><SessionBootstrap><RouterProvider router={router} /></SessionBootstrap></QueryClientProvider>
}

function SessionBootstrap({ children }: { children: ReactNode }) {
  const restore = useSessionStore(state => state.restore)
  const [ready, setReady] = useState(false)
  useEffect(() => { restore().finally(() => setReady(true)) }, [restore])
  if (!ready) return <main className="center-state" aria-busy="true"><strong>HRBPilot</strong><span>正在准备工作台…</span></main>
  return children
}
