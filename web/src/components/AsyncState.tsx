import type { ReactNode } from 'react'

export type AsyncKind = 'loading' | 'empty' | 'processing' | 'success' | 'error' | 'unauthorized'

const SYMBOL: Record<AsyncKind, ReactNode> = {
  loading: <span className="async-state__dots" aria-hidden="true"><i /><i /><i /></span>,
  processing: <span className="async-state__dots" aria-hidden="true"><i /><i /><i /></span>,
  empty: <span className="async-state__ring" aria-hidden="true" />,
  success: (
    <svg className="async-state__check" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 13l4 4 10-11" />
    </svg>
  ),
  error: '!',
  unauthorized: '×',
}

export function AsyncState({ kind, title, detail, action }: { kind: AsyncKind; title: string; detail?: string; action?: ReactNode }) {
  return (
    <section className={`async-state async-state--${kind}`} role={kind === 'error' ? 'alert' : 'status'} aria-busy={kind === 'loading' || kind === 'processing'}>
      <span className="async-state__symbol" aria-hidden="true">{SYMBOL[kind]}</span>
      <div><strong>{title}</strong>{detail && <p>{detail}</p>}{action}</div>
    </section>
  )
}
