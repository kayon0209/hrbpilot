import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { getWorkSummaries, type WorkSummary } from '../api/work-summaries'
import { AsyncState } from '../components/AsyncState'
import { useSessionStore } from '../app/session-store'
import { hasCapability } from '../app/roles'

/**
 * 今日工作 (spec §7.2) — the HRBP/manager home.
 *
 * Fixed order: 继续上次工作 → 需要你处理 → 开始一项工作 → 今天已完成.
 * Service health never appears here; a degraded capability shows only next to
 * its own entry. Empty buckets render nothing at all.
 */
export function TodayPage() {
  const role = useSessionStore(s => s.user?.role)
  const work = useQuery({ queryKey: ['work-summaries'], queryFn: getWorkSummaries })

  const firstActions = [
    { to: '/policy', title: '问一个制度问题', detail: '带制度依据的回答，可直接核对原文。' },
    ...(hasCapability(role, 'interview_digest')
      ? [{ to: '/interview', title: '整理一份面谈记录', detail: '把访谈记录转成结构化纪要和后续行动。' }]
      : []),
    ...(hasCapability(role, 'voice_insight')
      ? [{ to: '/voice', title: '分析一批员工反馈', detail: '从已脱敏反馈中归纳共同主题与信号。' }]
      : []),
  ]

  return (
    <main className="page-stack">
      <header className="page-heading">
        <div>
          <span className="eyebrow">今日工作</span>
          <h1>先做最重要的事</h1>
        </div>
      </header>

      {work.isPending && (
        <AsyncState kind="loading" title="正在整理你的工作" detail="读取最近的任务和草稿。" />
      )}
      {work.isError && (
        <AsyncState
          kind="error"
          title="无法读取最近工作"
          detail={work.error.message}
          action={<button onClick={() => work.refetch()}>重新读取</button>}
        />
      )}

      {work.data && (
        <>
          {work.data.continue_work && (
            <section className="panel" aria-labelledby="continue-heading">
              <h2 id="continue-heading">继续上次工作</h2>
              <ContinueCard item={work.data.continue_work} />
            </section>
          )}
          {work.data.attention.length > 0 && (
            <section className="panel" aria-labelledby="attention-heading">
              <h2 id="attention-heading">需要你处理</h2>
              <div className="issue-list">
                {work.data.attention.map(item => (
                  <WorkRow key={item.work_id} item={item} />
                ))}
              </div>
            </section>
          )}
          <section className="panel" aria-labelledby="start-heading">
            <h2 id="start-heading">开始一项工作</h2>
            <div className="start-actions">
              {firstActions.map(action => (
                <Link key={action.to} to={action.to} className="start-card">
                  <strong>{action.title}</strong>
                  <p>{action.detail}</p>
                </Link>
              ))}
            </div>
          </section>
          {work.data.completed_today.length > 0 && (
            <section className="panel" aria-labelledby="done-heading">
              <h2 id="done-heading">今天已完成</h2>
              <div className="issue-list">
                {work.data.completed_today.map(item => (
                  <WorkRow key={item.work_id} item={item} />
                ))}
              </div>
            </section>
          )}
        </>
      )}
    </main>
  )
}

function ContinueCard({ item }: { item: WorkSummary }) {
  return (
    <Link to={item.resume_target} className="continue-card">
      <div>
        <strong>{item.title}</strong>
        <p>{item.next_action}</p>
        <WorkMeta item={item} />
      </div>
      <span className="continue-cta">继续 →</span>
    </Link>
  )
}

function WorkRow({ item }: { item: WorkSummary }) {
  return (
    <article>
      <strong>{item.title}</strong>
      <p>{item.next_action}</p>
      <WorkMeta item={item} />
      <Link to={item.resume_target}>打开</Link>
    </article>
  )
}

function WorkMeta({ item }: { item: WorkSummary }) {
  const values = [
    item.business_status,
    item.waiting_for ? `等待：${item.waiting_for}` : null,
    item.owner ? `负责人：${item.owner}` : null,
    item.due_at ? `计划：${formatTime(item.due_at)}` : null,
    item.updated_at ? `更新：${formatTime(item.updated_at)}` : null,
  ].filter(Boolean)
  return <p className="work-meta">{values.join(' · ')}</p>
}

function formatTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '时间待确认' : date.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}
