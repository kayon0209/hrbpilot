import { useQuery } from '@tanstack/react-query'
import { getWorkSummaries } from '../../api/work-summaries'
import { AsyncState } from '../../components/AsyncState'

/**
 * 团队待处理 (spec §7.8) — aggregated overdue / blocked / high-impact items
 * the manager should intervene on. Shows WHY intervention is needed, the owner
 * and the waiting-for — never an unbounded list of sensitive employee bodies.
 *
 * Phase 3 interim: aggregates from the same work-summary read model, scoped
 * to what needs attention. Risk lifecycle and approvals land in Phase 3.
 */
export function TeamPendingPage() {
  const work = useQuery({ queryKey: ['work-summaries'], queryFn: getWorkSummaries })

  return (
    <main className="page-stack">
      <header className="page-heading">
        <div>
          <span className="eyebrow">团队治理</span>
          <h1>团队待处理</h1>
          <p>按影响和时间排序的介入点：每项说明原因、负责人和下一步，不展示员工敏感正文。</p>
        </div>
      </header>

      {work.isPending && <AsyncState kind="loading" title="正在聚合团队事项" detail="读取需要介入的工作。" />}
      {work.isError && (
        <AsyncState
          kind="error"
          title="无法读取团队事项"
          detail={work.error.message}
          action={<button onClick={() => work.refetch()}>重新读取</button>}
        />
      )}
      {work.data && work.data.attention.length === 0 && (
        <section className="panel">
          <h2>当前没有需要介入的事项</h2>
          <p>团队成员的工作都在正常推进。有新的失败、阻塞或到期事项时会出现在这里。</p>
        </section>
      )}
      {work.data && work.data.attention.length > 0 && (
        <section className="panel">
          <h2>需要介入</h2>
          <div className="issue-list">
            {work.data.attention.map(item => (
              <article key={item.work_id}>
                <strong>{item.title}</strong>
                <p>{item.next_action}</p>
                <small>更新于 {item.updated_at?.slice(0, 16).replace('T', ' ') || '—'}</small>
              </article>
            ))}
          </div>
        </section>
      )}
    </main>
  )
}
