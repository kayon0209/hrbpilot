import { type FormEvent, useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useSessionStore } from '../../app/session-store'
import styles from './LoginPage.module.css'

export function LoginPage() {
  const { login, user, pending, error } = useSessionStore()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  if (user) return <Navigate to="/" replace />
  async function submit(event: FormEvent) {
    event.preventDefault()
    try { await login(email, password); navigate((location.state as { from?: string } | null)?.from ?? '/') } catch { /* shown by store */ }
  }
  return <main className={styles.page}>
    <section className={styles.intro} aria-label="产品介绍">
      <span className={styles.kicker}>HR DECISION WORKBENCH</span>
      <h1>让制度、访谈与组织声音<br />成为可追溯的判断依据。</h1>
      <p>HRBPilot 将组织知识沉淀为可检索、可核验、可继续加工的工作材料。</p>
    </section>
    <section className={styles.card}>
      <div><span className={styles.mark}>H</span><strong>HRBPilot</strong></div>
      <h2>进入工作台</h2>
      <p>使用管理员为你创建的账号登录。</p>
      <form onSubmit={submit}>
        <label>邮箱<input name="email" type="email" autoComplete="username" value={email} onChange={e => setEmail(e.target.value)} required /></label>
        <label>密码<input name="password" type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></label>
        {error && <p className={styles.error} role="alert">{error}</p>}
        <button className="primary-button" disabled={pending}>{pending ? '正在登录…' : '登录'}</button>
      </form>
      <small>账号与权限由后端统一管理。</small>
    </section>
  </main>
}
