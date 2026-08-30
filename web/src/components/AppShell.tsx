import { useEffect, useRef, useState, type ReactNode } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useSessionStore } from '../app/session-store'
import { getVisibleNav, type NavItem } from '../app/navigation'
import styles from './AppShell.module.css'

const ROLE_LABELS: Record<string, string> = {
  employee: '员工',
  hrbp: 'HRBP',
  hr_manager: 'HR 经理',
  admin: '管理员',
}

/** Render nav grouped by `group`, headings only when the group changes. */
function grouped(items: NavItem[]) {
  const out: Array<{ heading: string | null; item: NavItem }> = []
  let last = ''
  for (const item of items) {
    out.push({ heading: item.group !== last ? item.group : null, item })
    last = item.group
  }
  return out
}

export function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useSessionStore()
  const navigate = useNavigate()
  const location = useLocation()
  const [open, setOpen] = useState(false)
  const menuButton = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!open) return
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setOpen(false)
        menuButton.current?.focus()
      }
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [open])
  const visibleNav = getVisibleNav(user?.role)
  const current = [...visibleNav]
    .sort((a, b) => b.to.length - a.to.length)
    .find(item => location.pathname === item.to || location.pathname.startsWith(item.to + '/'))
  return <div className={styles.layout}>
    <aside className={`${styles.sidebar} ${open ? styles.open : ''}`}>
      <div className={styles.brand}><span>H</span><div><strong>HRBPilot</strong><small>组织判断工作台</small></div></div>
      <nav id="primary-navigation" aria-label="主导航">{grouped(visibleNav).map(({ heading, item }) => <div key={item.to}>{heading && <p className={styles.group}>{heading}</p>}<NavLink to={item.to} end={item.to === '/'} onClick={() => setOpen(false)}>{item.label}</NavLink></div>)}</nav>
      <div className={styles.account}><span>{user?.name?.slice(0, 1) || 'U'}</span><div><strong>{user?.name}</strong><small>{user?.email}</small><small className={styles.role}>{ROLE_LABELS[user?.role ?? ''] ?? user?.role}</small></div></div>
    </aside>
    <div className={styles.main}><header className={styles.bar}><span className={styles.menuGroup}><button ref={menuButton} className={styles.menu} onClick={() => setOpen(v => !v)} aria-expanded={open} aria-controls="primary-navigation" aria-label="切换导航"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16" /></svg></button><span className={styles.crumb} aria-current="page">{current?.label ?? '今日工作'}</span></span><button className={styles.logout} onClick={() => { navigate('/login', { replace: true }); logout() }}>退出登录</button></header><div className={styles.content}>{children}</div></div>
  </div>
}
