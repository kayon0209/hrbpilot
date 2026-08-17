import { useState, type ReactNode } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { useSessionStore } from '../app/session-store'
import { hasMinimumRole } from '../app/roles'
import styles from './AppShell.module.css'

const nav = [
  { to: '/', label: '概览', group: '工作台' }, { to: '/policy', label: '制度问答', group: '知识' },
  { to: '/knowledge', label: '知识库', group: '知识' }, { to: '/interview', label: '面谈纪要', group: '人才', minimumRole: 'hrbp' },
  { to: '/voice', label: '员工声音', group: '人才', minimumRole: 'hrbp' }, { to: '/weekly', label: 'HR 周报', group: '创作', minimumRole: 'hrbp' },
  { to: '/culture', label: '文化内容', group: '创作', minimumRole: 'hrbp' }, { to: '/evaluation', label: '检索评测', group: '系统' },
  { to: '/settings', label: '服务设置', group: '系统', minimumRole: 'admin' },
]

export function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useSessionStore(); const navigate = useNavigate(); const [open, setOpen] = useState(false); let group = ''
  const visibleNav = nav.filter(item => !item.minimumRole || hasMinimumRole(user?.role, item.minimumRole))
  return <div className={styles.layout}>
    <aside className={`${styles.sidebar} ${open ? styles.open : ''}`}>
      <div className={styles.brand}><span>H</span><div><strong>HRBPilot</strong><small>组织判断工作台</small></div></div>
      <nav aria-label="主导航">{visibleNav.map(item => { const heading = item.group !== group ? (group = item.group) : null; return <div key={item.to}>{heading && <p className={styles.group}>{heading}</p>}<NavLink to={item.to} end={item.to === '/'} onClick={() => setOpen(false)}>{item.label}</NavLink></div> })}</nav>
      <div className={styles.account}><span>{user?.name?.slice(0, 1) || 'U'}</span><div><strong>{user?.name}</strong><small>{user?.email}</small></div></div>
    </aside>
    <div className={styles.main}><header className={styles.bar}><button className={styles.menu} onClick={() => setOpen(v => !v)} aria-label="切换导航">☰</button><span>组织知识与内容中枢</span><button className={styles.logout} onClick={() => { logout(); navigate('/login') }}>退出登录</button></header><div className={styles.content}>{children}</div></div>
  </div>
}
