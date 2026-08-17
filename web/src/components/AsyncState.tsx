import type { ReactNode } from 'react'

export type AsyncKind = 'loading' | 'empty' | 'processing' | 'success' | 'error' | 'unauthorized'
export function AsyncState({ kind, title, detail, action }: { kind: AsyncKind; title: string; detail?: string; action?: ReactNode }) {
  return <section className={`async-state async-state--${kind}`} role={kind === 'error' ? 'alert' : 'status'} aria-busy={kind === 'loading' || kind === 'processing'}>
    <span className="async-state__symbol" aria-hidden="true">{kind === 'error' ? '!' : kind === 'success' ? '✓' : kind === 'unauthorized' ? '×' : '·'}</span>
    <div><strong>{title}</strong>{detail && <p>{detail}</p>}{action}</div>
  </section>
}
