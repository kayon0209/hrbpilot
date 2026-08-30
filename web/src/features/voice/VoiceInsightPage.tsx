import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getVoiceHistory, getVoiceProgress, getVoiceResult, startVoiceAnalysis } from '../../api/async-scenarios'
import { AsyncState } from '../../components/AsyncState'
import { ResultDocument } from '../../components/ResultDocument'
import { PermissionNotice } from '../../components/PermissionNotice'
import { useSessionStore } from '../../app/session-store'
import { useTaskPolling } from '../async-workbench/useTaskPolling'
import styles from '../async-workbench/AsyncWorkbench.module.css'
import { hasMinimumRole } from '../../app/roles'

export function VoiceInsightPage() {
  const user = useSessionStore(s => s.user)
  const [content, setContent] = useState('')
  const [taskId, setTaskId] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false)
  const task = useTaskPolling(taskId, getVoiceProgress, getVoiceResult)
  const allowed = hasMinimumRole(user?.role, 'hrbp')
  const history = useQuery({ queryKey: ['voice-history'], queryFn: getVoiceHistory, enabled: allowed })
  const latest = history.data?.reports?.find(r => r.result) ?? null
  useEffect(() => { if (task.phase === 'completed') history.refetch() }, [task.phase]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!allowed) {
    return <main className="page-stack"><header className="page-heading"><div><h1>员工声音</h1></div></header><PermissionNotice feature="员工声音洞察" /></main>
  }

  async function start() {
    if (content.trim().length < 50) return setError('至少需要 50 字的有效反馈')
    setStarting(true)
    setError('')
    try {
      setTaskId((await startVoiceAnalysis(content.trim())).task_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : '分析未启动')
    } finally {
      setStarting(false)
    }
  }

  return <main className="page-stack"><header className="page-heading"><div><span className="eyebrow">员工声音</span><h1>员工声音</h1><p>从员工反馈中提取共同主题、情绪与可跟进事项。</p></div></header><div className={styles.grid}><section className={styles.editor}><div className={styles.toolbar}><strong>反馈语料</strong><small>不要粘贴不必要的个人敏感信息</small></div><label><span>员工反馈文本</span><textarea value={content} onChange={e => setContent(e.target.value)} rows={18} placeholder="粘贴已脱敏的调研、访谈或意见反馈…" /></label><div className={styles.actions}><small>{content.trim().length} 字</small><button className="primary-button" onClick={start} disabled={starting || task.phase === 'queued' || task.phase === 'processing'}>{starting ? '正在提交…' : '开始洞察'}</button></div>{error && <p className={styles.error}>{error}</p>}</section><section className={styles.result}><h2>洞察报告</h2>{task.phase === 'idle' && !latest && <AsyncState kind="empty" title="等待分析材料" detail="输入已脱敏的员工反馈文本。" />}{task.phase === 'idle' && latest?.result != null && <><p className={styles.historyNote}>最近完成于 {formatTime(latest.completed_at ?? latest.created_at)}</p><ResultDocument result={latest.result as Record<string, unknown>} /></>}{task.phase === 'queued' && <AsyncState kind="processing" title="排队中" detail="任务已提交，正在等待分析。" />}{task.phase === 'processing' && <AsyncState kind="processing" title="正在归纳" detail="任务在后端运行，完成后会自动显示结果。" />}{task.phase === 'error' && <AsyncState kind="error" title="洞察失败" detail={task.error ?? undefined} action={<button onClick={task.retry}>重试读取</button>} />}{task.result && <ResultDocument result={task.result} />}</section></div></main>
}

function formatTime(value: unknown) { const date = value ? new Date(String(value)) : null; return date && !Number.isNaN(date.getTime()) ? date.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—' }
