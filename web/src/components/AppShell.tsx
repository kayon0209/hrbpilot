import { useState, type ReactNode } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { useSessionStore } from '../app/session-store'
import { getVisibleNav } from '../app/navigation'
import styles from './AppShell.module.css'

export function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useSessionStore(); const navigate = useNavigate(); const [open, setOpen] = useState(false); let group = ''
  const visibleNav = getVisibleNav(user?.role)
  return <div className={styles.layout}>
    <aside className={`${styles.sidebar} ${open ? styles.open : ''}`}>
      <div className={styles.brand}><span>H</span><div><strong>HRBPilot</strong><small>组织判断工作台</small></div></div>
      <nav aria-label="主导航">{visibleNav.map(item => { const heading = item.group !== group ? (group = item.group) : null; return <div key={item.to}>{heading && <p className={styles.group}>{heading}</p>}<NavLink to={item.to} end={item.to === '/'} onClick={() => setOpen(false)}>{item.label}</NavLink></div> })}</nav>
      <div className={styles.account}><span>{user?.name?.slice(0, 1) || 'U'}</span><div><strong>{user?.name}</strong><small>{user?.email}</small></div></div>
    </aside>
    <div className={styles.main}><header className={styles.bar}><button className={styles.menu} onClick={() => setOpen(v => !v)} aria-label="切换导航">☰</button><span>组织知识与内容中枢</span><button className={styles.logout} onClick={() => { logout(); navigate('/login') }}>退出登录</button></header><div className={styles.content}>{children}</div></div>
  </div>
}
