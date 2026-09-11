import { useQuery } from '@tanstack/react-query'
import { listAuditEvents } from '../../api/audit'
import { AsyncState } from '../../components/AsyncState'

const ACTION_LABELS: Record<string, string> = {
  'data_source.created': '登记材料来源',
  'data_source.paused': '暂停材料同步',
  'data_source.resumed': '恢复材料同步',
  'data_source.revoked': '撤销材料授权',
  'employee_request.triaged': '处理员工请求',
  'knowledge_feedback.decided': '处理知识反馈',
  'weekly_report.saved': '保存周报',
  'weekly_report.published': '发布周报',
}

/** Human-readable lines for an event's details payload (audit P2-4). */
function detailLines(details: Record<string, unknown>): string[] {
  const out: string[] = []
  for (const [key, value] of Object.entries(details)) {
    if (value === null || value === undefined || value === '' || key === 'text') continue
    out.push(`${DETAIL_KEY_LABELS[key] ?? key}：${String(value)}`)
  }
  return out
}

const DETAIL_KEY_LABELS: Record<string, string> = {
  platform: '平台',
  reason: '原因',
  status: '处理状态',
  owner_id: '负责人',
  decision: '决策',
  assignee: '指派给',
}

export function AuditPage() {
  const query = useQuery({ queryKey: ['audit-events'], queryFn: listAuditEvents })
  return <main className="page-stack">
    <header className="page-heading"><div><span className="eyebrow">管理后台</span><h1>审计记录</h1><p>查看授权、发布和人工决策等敏感操作。记录只追加，不在本页修改。</p></div></header>
    {query.isPending && <AsyncState kind="loading" title="正在读取审计记录" />}
    {query.isError && <AsyncState kind="error" title="审计记录读取失败" detail={query.error.message} action={<button onClick={() => query.refetch()}>重试</button>} />}
    {query.data && query.data.events.length === 0 && <AsyncState kind="empty" title="还没有敏感操作记录" detail="发生授权、发布或人工决策后会显示在这里。" />}
    {query.data && query.data.events.length > 0 && <section className="panel"><div className="issue-list">{query.data.events.map(event => <article key={event.event_id}>
      <strong>{ACTION_LABELS[event.action] ?? '系统操作'}</strong>
      <p>{event.object_type ?? '对象'} · {event.object_id ?? '标识缺失'}</p>
      {event.input_summary && <p>内容摘要：{event.input_summary}</p>}
      {detailLines(event.details).map(line => <p key={line}>{line}</p>)}
      <small>操作人：{event.actor_name ?? event.actor_email ?? event.actor_id} · {new Date(event.created_at).toLocaleString('zh-CN')}</small>
    </article>)}</div></section>}
  </main>
}
