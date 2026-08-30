import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { getWorkSummaries, type WorkSummary } from '../../api/work-summaries'
import { AsyncState } from '../../components/AsyncState'

/**
 * 工作任务 (spec §7.5) — each task shows business stage, next step, owner,
 * waiting-for, deadline. Real-unit progress (x/y) only when a true denominator
 * exists; otherwise stage words. No badges, points or streaks.
 */
export function TasksPage() {
  const work = useQuery({ queryKey: ['work-summaries'], queryFn: getWorkSummaries })

  const open: WorkSummary[] = []
  const done: WorkSummary[] = []
  if (work.data) {
    // Deduplicate by work_id (audit P1-3): continue_work and attention share
    // the newest actionable object — rendering both produced duplicate rows
    // and duplicate React keys.
    const seen = new Set<string>()
    for (const item of [...(work.data.continue_work ? [work.data.continue_work] : []), ...work.data.attention]) {
      if (seen.has(item.work_id)) continue
      seen.add(item.work_id)
      open.push(item)
    }
    for (const item of work.data.completed_today) {
      if (seen.has(item.work_id)) continue
      seen.add(item.work_id)
      done.push(item)
    }
  }

  return (
    <main className="page-stack">
      <header className="page-heading">
        <div>
          <span className="eyebrow">工作台</span>
          <h1>工作任务</h1>
          <p>每项任务标明阶段、下一步与等待对象；完成反馈直接指向产出。</p>
        </div>
      </header>

      {work.isPending && <AsyncState kind="loading" title="正在读取任务" detail="按更新时间聚合各来源。" />}
      {work.isError && (
        <AsyncState
          kind="error"
          title="无法读取任务"
          detail={work.error.message}
          action={<button onClick={() => work.refetch()}>重新读取</button>}
        />
      )}
      {work.data && open.length === 0 && done.length === 0 && (
        <section className="panel">
          <h2>还没有进行中的任务</h2>
          <p>从今日工作的三个首行动作开始：问制度、整理面谈、分析反馈。</p>
          <div className="admin-links">
            <Link to="/policy">问制度</Link>
          </div>
        </section>
      )}
      {open.length > 0 && (
        <section className="panel">
          <h2>进行中</h2>
          <div className="issue-list">
            {open.map(item => (
              <article key={item.work_id}>
                <strong>{item.title}</strong>
                <p>{item.next_action}</p>
                <span className={`task-stage task-stage--${item.business_status}`}>{item.business_status}</span>
                <Link to={item.resume_target}>打开</Link>
              </article>
            ))}
          </div>
        </section>
      )}
      {done.length > 0 && (
        <section className="panel">
          <h2>今天已完成</h2>
          <div className="issue-list">
            {done.map(item => (
              <article key={item.work_id}>
                <strong>{item.title}</strong>
                <p>{item.next_action}</p>
              </article>
            ))}
          </div>
        </section>
      )}
    </main>
  )
}
