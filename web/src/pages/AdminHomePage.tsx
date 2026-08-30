import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { getReadiness } from '../api/system'
import { AsyncState } from '../components/AsyncState'

/**
 * 管理首页 (spec §7.10) — only what needs ADMIN ACTION:
 * failures, permission risks, failed syncs, pending configuration.
 * Healthy services are never stacked into metric cards.
 */
export function AdminHomePage() {
  const ready = useQuery({ queryKey: ['readiness'], queryFn: getReadiness, refetchInterval: 60_000 })
  const checks = Object.entries(ready.data?.checks ?? {})
  const failed = checks.filter(([, v]) => v === false || v === 'error' || (typeof v === 'object' && v.status && v.status !== 'ok'))

  return (
    <main className="page-stack">
      <header className="page-heading">
        <div>
          <span className="eyebrow">管理后台</span>
          <h1>需要管理员处理的事项</h1>
          <p>这里只列出需要行动的故障与配置；运行正常的服务不占用你的注意力。</p>
        </div>
      </header>

      {ready.isPending && <AsyncState kind="loading" title="正在检查系统状态" detail="读取各依赖的运行状况。" />}
      {ready.isError && (
        <AsyncState
          kind="error"
          title="无法读取系统状态"
          detail={ready.error.message}
          action={<button onClick={() => ready.refetch()}>重新检查</button>}
        />
      )}
      {ready.data && failed.length === 0 && (
        <section className="panel">
          <h2>当前没有需要处理的故障</h2>
          <p>各依赖运行正常。你可以前往 AI 质量查看评测指标，或在服务设置中调整配置。</p>
          <div className="admin-links">
            <Link to="/evaluation">查看 AI 质量</Link>
            <Link to="/settings">服务设置</Link>
          </div>
        </section>
      )}
      {failed.length > 0 && (
        <section className="panel">
          <h2>故障与恢复</h2>
          <div className="issue-list">
            {failed.map(([name, value]) => (
              <article key={name}>
                <strong>{name} 不可用</strong>
                <p>{typeof value === 'object' && value && 'detail' in value ? String((value as { detail?: string }).detail ?? '连接检查未通过') : '连接检查未通过'}</p>
                <small>受影响的功能会向对应入口的使用者说明影响；这里提供恢复动作。</small>
              </article>
            ))}
          </div>
          <div className="admin-links">
            <Link to="/settings">前往服务设置</Link>
          </div>
        </section>
      )}
    </main>
  )
}
