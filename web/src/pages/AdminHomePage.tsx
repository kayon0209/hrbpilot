import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { getReadiness } from '../api/system'
import { AsyncState } from '../components/AsyncState'

/**
 * 管理首页 (spec §7.10) — only what needs ADMIN ACTION:
 * failures, permission risks, failed syncs, pending configuration.
 * Healthy services are never stacked into metric cards.
 */

/**
 * 依赖 → 中文名 + 真实影响面。
 *
 * 影响面按代码事实写，不写「服务异常」这类废话：管理员要判断的是
 * 「要不要现在处理」，而这取决于坏掉的东西到底挡住了什么。
 * 依据见 docs/operations/dependency-failure-matrix.md。
 */
const DEPENDENCIES: Record<string, { label: string; impact: string }> = {
  database: {
    label: '数据库',
    impact: '所有业务读写都不可用，服务无法对外提供。需要立即恢复。',
  },
  redis: {
    label: '缓存与限流',
    impact: '限流是 fail-closed：Redis 不可用会让已登录请求全部被拒绝（429）。需要立即恢复。',
  },
  milvus: {
    label: '向量库',
    impact: '密集检索不可用，混合检索自动降级为关键词检索 —— 问答仍可用，但召回会下降。',
  },
  minio: {
    label: '对象存储',
    impact: '附件与文件的上传下载不可用，其余功能不受影响。',
  },
  embedding: {
    label: '向量化服务',
    impact: '入库向量化与结果重排不可用；关键词检索与已有向量仍可用。',
  },
}

function describe(name: string) {
  return DEPENDENCIES[name] ?? { label: name, impact: '连接检查未通过，影响范围未知。' }
}

export function AdminHomePage() {
  const ready = useQuery({ queryKey: ['readiness'], queryFn: getReadiness, refetchInterval: 60_000 })
  const data = ready.data
  const critical = data?.critical_failed ?? []
  const optional = data?.optional_unavailable ?? []

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

      {data && critical.length === 0 && optional.length === 0 && (
        <section className="panel">
          <h2>当前没有需要处理的故障</h2>
          <p>各依赖运行正常。你可以前往 AI 质量查看评测指标，或在服务设置中调整配置。</p>
          <div className="admin-links">
            <Link to="/evaluation">查看 AI 质量</Link>
            <Link to="/settings">服务设置</Link>
          </div>
        </section>
      )}

      {critical.length > 0 && (
        <section className="panel">
          <h2>
            服务不可用 <span className="status-badge status-badge--error">需立即处理</span>
          </h2>
          <p>关键依赖故障，当前实例无法对外提供服务。编排系统会把它摘出流量。</p>
          <div className="issue-list">
            {critical.map((name) => (
              <article key={name}>
                <strong>{describe(name).label}不可用</strong>
                <p>{describe(name).impact}</p>
              </article>
            ))}
          </div>
          <div className="admin-links">
            <Link to="/settings">前往服务设置</Link>
          </div>
        </section>
      )}

      {optional.length > 0 && (
        <section className="panel">
          <h2>
            可选能力未就绪 <span className="status-badge status-badge--parsing">服务仍可运行</span>
          </h2>
          <p>这些依赖只影响对应能力，不影响整体可用性。若本次部署本就不含它们，可以忽略。</p>
          <div className="issue-list">
            {optional.map((name) => (
              <article key={name}>
                <strong>{describe(name).label}不可用</strong>
                <p>{describe(name).impact}</p>
              </article>
            ))}
          </div>
        </section>
      )}
    </main>
  )
}
