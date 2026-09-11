import { useEffect, useMemo, useState } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import {
  getVoiceEntriesPaged,
  getVoiceEntry,
  getVoiceHistory,
  getVoiceProgress,
  getVoiceResult,
  startVoiceAnalysis,
  type MaterialSummary,
} from '../../api/async-scenarios'
import { AsyncState } from '../../components/AsyncState'
import { ResultDocument } from '../../components/ResultDocument'
import { PermissionNotice } from '../../components/PermissionNotice'
import { useSessionStore } from '../../app/session-store'
import { useTaskPolling } from '../async-workbench/useTaskPolling'
import { BatchPanel } from '../interview/BatchPanel'
import styles from '../async-workbench/AsyncWorkbench.module.css'
import { hasMinimumRole } from '../../app/roles'

const CHANNELS: Record<string, string> = {
  survey: '调研问卷',
  inbox: '意见箱',
  townhall: '座谈会',
  interview: '访谈',
}

function severityBadgeClass(level: unknown) {
  if (level === 'HIGH') return 'badge badge--high'
  if (level === 'MEDIUM') return 'badge badge--medium'
  if (level === 'LOW') return 'badge badge--low'
  return 'badge'
}

function severityLabel(level: unknown) {
  if (level === 'HIGH') return '严重'
  if (level === 'MEDIUM') return '中等'
  if (level === 'LOW') return '轻微'
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

/** 材料行：列表只渲染摘要字段；点开后才加载完整报告（按需详情）。 */
function EntryRow({ entry }: { entry: MaterialSummary }) {
  const [open, setOpen] = useState(false)
  const detail = useQuery({
    queryKey: ['voice-entry', entry.entry_id],
    queryFn: () => getVoiceEntry(entry.entry_id!),
    enabled: open,
  })
  const pending = statusLabel(entry.status)
  return (
    <div className="material-row-wrap">
      <button type="button" className="material-row" onClick={() => setOpen(v => !v)} aria-expanded={open}>
        <span className="material-row-main">
          <span className="material-row-name">{entry.employee_name || '匿名调研'}</span>
          <span className="badge">{CHANNELS[entry.channel ?? 'survey'] ?? '调研问卷'}</span>
          <span className={`badge ${open ? 'badge--medium' : ''}`}>{formatDay(entry.created_at)}</span>
          <span className={severityBadgeClass(entry.top_severity)}>{severityLabel(entry.top_severity)}</span>
          {pending && <span className="material-status-note">{pending}</span>}
        </span>
        {entry.summary && <p className="material-row-summary">{entry.summary}</p>}
      </button>
      {open && (
        <div className="material-detail">
          {detail.isPending && <AsyncState kind="loading" title="正在读取完整报告" />}
          {detail.isError && (
            <AsyncState
              kind="error"
              title="无法读取完整报告"
              detail={detail.error.message}
              action={<button onClick={() => detail.refetch()}>重试</button>}
            />
          )}
          {detail.data?.result != null && <ResultDocument result={detail.data.result} />}
          {detail.data?.result == null && detail.isSuccess && (
            <AsyncState kind="empty" title="暂无洞察报告" detail="该条目尚未完成分析或结果解析失败。" />
          )}
        </div>
      )}
    </div>
  )
}

export function VoiceInsightPage() {
  const user = useSessionStore(s => s.user)
  const [content, setContent] = useState('')
  const [employeeName, setEmployeeName] = useState('')
  const [channel, setChannel] = useState('survey')
  const [taskId, setTaskId] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false)
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [searchParams, setSearchParams] = useSearchParams()
  const filterChannel = searchParams.get('channel') ?? ''
  const filterStatus = searchParams.get('status') ?? ''
  const task = useTaskPolling(taskId, getVoiceProgress, getVoiceResult)
  const allowed = hasMinimumRole(user?.role, 'hrbp')
  // Only the newest completed report powers the right-hand panel.
  const latestQuery = useQuery({ queryKey: ['voice-history'], queryFn: () => getVoiceHistory(1), enabled: allowed })
  const latest = latestQuery.data?.reports?.find(r => r.result) ?? null
  const voiceFilters = useMemo(() => ({ channel: filterChannel, status: filterStatus }), [filterChannel, filterStatus])
  const entries = useInfiniteQuery({
    queryKey: ['voice-entries', query, voiceFilters],
    queryFn: ({ pageParam }) => getVoiceEntriesPaged(query, voiceFilters, pageParam ?? ''),
    initialPageParam: '',
    getNextPageParam: lastPage => lastPage.next_cursor,
    enabled: allowed,
  })
  useEffect(() => { if (task.phase === 'completed') { latestQuery.refetch(); entries.refetch() } }, [task.phase]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!allowed) {
    return <main className="page-stack"><header className="page-heading"><div><h1>员工声音</h1></div></header><PermissionNotice feature="员工声音洞察" /></main>
  }

  async function start() {
    if (content.trim().length < 50) return setError('至少需要 50 字的有效反馈')
    setStarting(true)
    setError('')
    try {
      setTaskId((await startVoiceAnalysis(content.trim(), {
        employee_name: employeeName.trim() || undefined,
        channel,
      })).task_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : '分析未启动')
    } finally {
      setStarting(false)
    }
  }

  const flatEntries = entries.data?.pages.flatMap(page => page.entries) ?? []

  return <main className="page-stack"><header className="page-heading"><div><span className="eyebrow">员工声音</span><h1>员工声音</h1><p>从员工反馈中提取共同主题、情绪与可跟进事项；条目自动入库，可按员工查找。</p></div></header><div className={styles.grid}><section className={styles.editor}><div className={styles.toolbar}><strong>反馈语料</strong><small>不要粘贴不必要的个人敏感信息</small></div><div className="task-metadata"><label>员工姓名<span className="form-hint">匿名调研可留空</span><input value={employeeName} onChange={e => setEmployeeName(e.target.value)} placeholder="例：李四" /></label><label>来源渠道<select value={channel} onChange={e => setChannel(e.target.value)}>{Object.entries(CHANNELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div><label><span>员工反馈文本</span><textarea value={content} onChange={e => setContent(e.target.value)} rows={14} placeholder="粘贴已脱敏的调研、访谈或意见反馈…" /></label><div className={styles.actions}><small>{content.trim().length} 字</small><button className="primary-button" onClick={start} disabled={starting || task.phase === 'queued' || task.phase === 'processing'}>{starting ? '正在提交…' : '开始洞察'}</button></div>{error && <p className={styles.error}>{error}</p>}</section><section className={styles.result}><h2>洞察报告</h2>{task.phase === 'idle' && !latest && <AsyncState kind="empty" title="等待分析材料" detail="输入已脱敏的员工反馈文本。" />}{task.phase === 'idle' && latest?.result != null && <><p className={styles.historyNote}>最近完成于 {formatTime(latest.completed_at ?? latest.created_at)}</p><ResultDocument result={latest.result as Record<string, unknown>} /></>}{task.phase === 'queued' && <AsyncState kind="processing" title="排队中" detail="任务已提交，正在等待分析。" />}{task.phase === 'processing' && <AsyncState kind="processing" title="正在归纳" detail="任务在后端运行，完成后会自动显示结果。" />}{task.phase === 'error' && <AsyncState kind="error" title="洞察失败" detail={task.error ?? undefined} action={<button onClick={task.retry}>重试读取</button>} />}{task.result && <ResultDocument result={task.result} />}</section></div>

    <section className="panel">
      <h2>声音条目</h2>
      <div className="material-toolbar">
        <input
          className="material-search"
          value={search}
          onChange={e => setSearch(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') setQuery(search.trim()) }}
          placeholder="搜索员工姓名、渠道或正文关键词，回车确认"
          aria-label="搜索员工声音条目"
        />
        <select value={filterChannel} onChange={e => { const v = e.target.value; if (v) searchParams.set('channel', v); else searchParams.delete('channel'); setSearchParams(searchParams); }} aria-label="渠道筛选">
          <option value="">全部渠道</option>
          <option value="survey">调研问卷</option>
          <option value="inbox">意见箱</option>
          <option value="townhall">座谈会</option>
          <option value="interview">访谈</option>
        </select>
        <select value={filterStatus} onChange={e => { const v = e.target.value; if (v) searchParams.set('status', v); else searchParams.delete('status'); setSearchParams(searchParams); }} aria-label="状态筛选">
          <option value="">全部状态</option>
          <option value="analyzing">分析中</option>
          <option value="completed">已完成</option>
          <option value="failed">失败</option>
        </select>
        {query && <button type="button" className="link-button" onClick={() => { setSearch(''); setQuery('') }}>清除搜索</button>}
        {(filterChannel || filterStatus) && <button type="button" className="link-button" onClick={() => { searchParams.delete('channel'); searchParams.delete('status'); setSearchParams(searchParams); }}>清除筛选</button>}
      </div>
      {entries.isPending && <AsyncState kind="loading" title="正在读取条目" />}
      {entries.isError && (
        <AsyncState
          kind="error"
          title="无法读取条目列表"
          detail={entries.error.message}
          action={<button onClick={() => entries.refetch()}>重试</button>}
        />
      )}
      {entries.isSuccess && flatEntries.length === 0 && (
        <AsyncState kind="empty" title={query ? '没有匹配的条目' : '还没有入库条目'} detail={query ? '换个关键词，或清除筛选后重试。' : '完成一次洞察分析后，条目会自动出现在这里。'} />
      )}
      {flatEntries.length > 0 && (
        <div className="material-list">
          {flatEntries.map(entry => <EntryRow key={entry.entry_id} entry={entry} />)}
        </div>
      )}
      {entries.hasNextPage && (
        <div className="material-more">
          <button type="button" onClick={() => entries.fetchNextPage()} disabled={entries.isFetchingNextPage}>
            {entries.isFetchingNextPage ? '正在加载…' : '加载更多'}
          </button>
        </div>
      )}
    </section>
    <BatchPanel type="voice" />
  </main>
}

function formatTime(value: unknown) { const date = value ? new Date(String(value)) : null; return date && !Number.isNaN(date.getTime()) ? date.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—' }
