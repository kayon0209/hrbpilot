import { useEffect, useState } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import {
  getInterviewHistory,
  getInterviewProgress,
  getInterviewRecord,
  getInterviewRecords,
  getInterviewResult,
  startInterviewAnalysis,
  uploadInterviewDocument,
  type MaterialSummary,
} from '../../api/async-scenarios'
import { AsyncState } from '../../components/AsyncState'
import { ResultDocument } from '../../components/ResultDocument'
import { useSessionStore } from '../../app/session-store'
import { PermissionNotice } from '../../components/PermissionNotice'
import { useTaskPolling } from '../async-workbench/useTaskPolling'
import styles from '../async-workbench/AsyncWorkbench.module.css'
import { hasMinimumRole } from '../../app/roles'

const INTERVIEW_TYPES: Record<string, string> = {
  general: '综合面谈',
  performance: '绩效面谈',
  exit: '离职面谈',
  onboarding: '入职面谈',
  communication: '日常沟通',
}

function riskBadgeClass(level: unknown) {
  if (level === 'HIGH') return 'badge badge--high'
  if (level === 'MEDIUM') return 'badge badge--medium'
  if (level === 'LOW') return 'badge badge--low'
  return 'badge'
}

function riskLabel(level: unknown) {
  if (level === 'HIGH') return '高风险'
  if (level === 'MEDIUM') return '中风险'
  if (level === 'LOW') return '低风险'
  return '未出结果'
}

function statusLabel(status: string) {
  if (status === 'completed') return ''
  if (status === 'failed') return '分析失败'
  return '分析中…'
}

function formatDay(value: string | null) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}

/** 材料行：列表只渲染摘要字段；点开后才加载完整结果（按需详情）。 */
function RecordRow({ record }: { record: MaterialSummary }) {
  const [open, setOpen] = useState(false)
  const detail = useQuery({
    queryKey: ['interview-record', record.record_id],
    queryFn: () => getInterviewRecord(record.record_id!),
    enabled: open,
  })
  const pending = statusLabel(record.status)
  return (
    <div className="material-row-wrap">
      <button type="button" className="material-row" onClick={() => setOpen(v => !v)} aria-expanded={open}>
        <span className="material-row-main">
          <span className="material-row-name">{record.employee_name || '未标注员工'}</span>
          <span className={`badge ${open ? 'badge--medium' : ''}`}>{INTERVIEW_TYPES[record.interview_type ?? 'general'] ?? '综合面谈'}</span>
          {record.interview_date && <span className="material-row-meta">{formatDay(record.interview_date)}</span>}
          <span className={riskBadgeClass(record.risk_level)}>{riskLabel(record.risk_level)}</span>
          {pending && <span className="material-status-note">{pending}</span>}
        </span>
        {record.summary && <p className="material-row-summary">{record.summary}</p>}
      </button>
      {open && (
        <div className="material-detail">
          {detail.isPending && <AsyncState kind="loading" title="正在读取完整纪要" />}
          {detail.isError && (
            <AsyncState
              kind="error"
              title="无法读取完整纪要"
              detail={detail.error.message}
              action={<button onClick={() => detail.refetch()}>重试</button>}
            />
          )}
          {detail.data?.result != null && <ResultDocument result={detail.data.result} />}
          {detail.data?.result == null && detail.isSuccess && (
            <AsyncState kind="empty" title="暂无结构化结果" detail="该记录尚未完成分析或结果解析失败。" />
          )}
        </div>
      )}
    </div>
  )
}

export function InterviewDigestPage() {
  const user = useSessionStore(s => s.user)
  const [content, setContent] = useState('')
  const [employeeName, setEmployeeName] = useState('')
  const [title, setTitle] = useState('')
  const [interviewDate, setInterviewDate] = useState('')
  const [taskId, setTaskId] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false)
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const task = useTaskPolling(taskId, getInterviewProgress, getInterviewResult)
  const allowed = hasMinimumRole(user?.role, 'hrbp')
  // Only the newest completed result powers the right-hand panel — one full
  // JSON payload, not the whole history (list rows below are summaries).
  const latestQuery = useQuery({ queryKey: ['interview-history'], queryFn: () => getInterviewHistory(1), enabled: allowed })
  const latest = latestQuery.data?.digests?.find(d => d.result) ?? null
  const records = useInfiniteQuery({
    queryKey: ['interview-records', query],
    queryFn: ({ pageParam }) => getInterviewRecords(query, pageParam ?? ''),
    initialPageParam: '',
    getNextPageParam: lastPage => lastPage.next_cursor,
    enabled: allowed,
  })
  useEffect(() => { if (task.phase === 'completed') { latestQuery.refetch(); records.refetch() } }, [task.phase]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!allowed) return <main className="page-stack"><header className="page-heading"><div><h1>面谈纪要</h1></div></header><PermissionNotice feature="面谈纪要" /></main>

  async function start() {
    if (content.trim().length < 50) return setError('至少需要50字')
    setStarting(true)
    setError('')
    try {
      const result = await startInterviewAnalysis(content.trim(), {
        employee_name: employeeName.trim() || undefined,
        title: title.trim() || undefined,
        interview_date: interviewDate || undefined,
      })
      setTaskId(result.task_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : '分析未启动')
    } finally {
      setStarting(false)
    }
  }
  async function upload(file?: File) { if (!file) return; try { const result = await uploadInterviewDocument(file); setContent(result.content) } catch (e) { setError(e instanceof Error ? e.message : '文件解析失败') } }

  const flatRecords = records.data?.pages.flatMap(page => page.records) ?? []

  return <main className="page-stack">
    <header className="page-heading"><div><span className="eyebrow">面谈整理</span><h1>面谈纪要</h1><p>先核对原文，再提交异步分析；材料自动入库，可按员工查找。</p></div></header>
    <div className={styles.grid}>
      <section className={styles.editor}>
        <div className={styles.toolbar}><strong>面谈原文</strong><label className="secondary-button">导入文件<input hidden type="file" accept=".txt,.pdf,.docx" onChange={e => upload(e.target.files?.[0])} /></label></div>
        <div className="task-metadata">
          <label>员工姓名<span className="form-hint">选填，便于按人查找</span><input value={employeeName} onChange={e => setEmployeeName(e.target.value)} placeholder="例：张三" /></label>
          <label>标题<span className="form-hint">选填</span><input value={title} onChange={e => setTitle(e.target.value)} placeholder="例：季度绩效沟通" /></label>
          <label>访谈日期<span className="form-hint">选填</span><input type="date" value={interviewDate} onChange={e => setInterviewDate(e.target.value)} /></label>
        </div>
        <label><span>面谈内容</span><textarea value={content} onChange={e => setContent(e.target.value)} rows={14} placeholder="粘贴访谈记录，至少 50 字…" /></label>
        <div className={styles.actions}><small>{content.trim().length} 字</small><button className="primary-button" onClick={start} disabled={starting || task.phase === 'queued' || task.phase === 'processing'}>{starting ? '正在提交…' : '开始分析'}</button></div>
        {error && <p className={styles.error} role="alert">{error}</p>}
      </section>
      <section className={styles.result}>
        <h2>结构化纪要</h2>
        {task.phase === 'idle' && !latest && <AsyncState kind="empty" title="还没有分析结果" detail="完整输入面谈记录后开始分析。" />}
        {task.phase === 'idle' && latest?.result != null && <><p className={styles.historyNote}>最近完成于 {formatTime(latest.completed_at ?? latest.created_at)}</p><ResultDocument result={latest.result as Record<string, unknown>} /></>}
        {task.phase === 'queued' && <AsyncState kind="processing" title="排队中" detail="任务已提交，正在等待分析。" />}
        {task.phase === 'processing' && <AsyncState kind="processing" title="正在分析" detail="任务在后端运行，完成后会自动显示结果。" />}
        {task.phase === 'error' && <AsyncState kind="error" title="分析失败" detail={task.error ?? undefined} action={<button onClick={task.retry}>重试读取</button>} />}
        {task.result && <ResultDocument result={task.result} />}
      </section>
    </div>

    <section className="panel">
      <h2>历史材料</h2>
      <div className="material-toolbar">
        <input
          className="material-search"
          value={search}
          onChange={e => setSearch(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') setQuery(search.trim()) }}
          placeholder="搜索员工姓名、标题或正文关键词，回车确认"
          aria-label="搜索面谈材料"
        />
        {query && <button type="button" className="link-button" onClick={() => { setSearch(''); setQuery('') }}>清除</button>}
      </div>
      {records.isPending && <AsyncState kind="loading" title="正在读取材料" />}
      {records.isError && (
        <AsyncState
          kind="error"
          title="无法读取材料列表"
          detail={records.error.message}
          action={<button onClick={() => records.refetch()}>重试</button>}
        />
      )}
      {records.isSuccess && flatRecords.length === 0 && (
        <AsyncState kind="empty" title={query ? '没有匹配的材料' : '还没有入库材料'} detail={query ? '换个关键词，或清除筛选后重试。' : '完成一次面谈分析后，材料会自动出现在这里。'} />
      )}
      {flatRecords.length > 0 && (
        <div className="material-list">
          {flatRecords.map(record => <RecordRow key={record.record_id} record={record} />)}
        </div>
      )}
      {records.hasNextPage && (
        <div className="material-more">
          <button type="button" onClick={() => records.fetchNextPage()} disabled={records.isFetchingNextPage}>
            {records.isFetchingNextPage ? '正在加载…' : '加载更多'}
          </button>
        </div>
      )}
    </section>
  </main>
}

function formatTime(value: unknown) { const date = value ? new Date(String(value)) : null; return date && !Number.isNaN(date.getTime()) ? date.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—' }
