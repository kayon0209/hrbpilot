import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { generateWeeklyReport, getWeeklyHistory, getWeeklySources, saveWeeklyReport, type WeeklyHistoryItem } from '../../api/content-workflows'
import { AsyncState } from '../../components/AsyncState'
import { ResultDocument } from '../../components/ResultDocument'
import { PermissionNotice } from '../../components/PermissionNotice'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { useSessionStore } from '../../app/session-store'
import styles from './WeeklyReportPage.module.css'
import { hasMinimumRole } from '../../app/roles'

export function WeeklyReportPage() {
  const user = useSessionStore(s => s.user)
  const [period, setPeriod] = useState(currentWeek())
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<{ report_id: string; report: Record<string, unknown> } | null>(null)
  const [notice, setNotice] = useState('')
  const [confirmPublish, setConfirmPublish] = useState(false)
  const [selected, setSelected] = useState<string[]>([])
  const sources = useQuery({ queryKey: ['weekly-sources'], queryFn: getWeeklySources })
  const history = useQuery({ queryKey: ['weekly-history'], queryFn: getWeeklyHistory })

  if (!hasMinimumRole(user?.role, 'hrbp')) {
    return <main className="page-stack"><header className="page-heading"><h1>HR 周报</h1></header><PermissionNotice feature="HR 周报" /></main>
  }

  function toggleSource(id: string) {
    setSelected(list => list.includes(id) ? list.filter(item => item !== id) : [...list, id])
  }

  async function generate() {
    if (!selected.length) return setError('请至少选择一个数据来源')
    setPending(true)
    setError('')
    setNotice('')
    try {
      const response = await generateWeeklyReport({ period, source_ids: selected, draft_mode: true })
      setResult(response)
      setNotice('草稿已生成，可以先检查再保存。')
      history.refetch()
    } catch (e) {
      setError(e instanceof Error ? e.message : '生成失败')
    } finally {
      setPending(false)
    }
  }

  async function save(action: 'save' | 'publish') {
    if (!result) return
    try {
      await saveWeeklyReport({ report_id: result.report_id, action, edits: result.report })
      setConfirmPublish(false)
      setNotice(action === 'publish' ? '周报已发布' : '草稿已保存')
      history.refetch()
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败')
    }
  }

  function openHistory(item: WeeklyHistoryItem) {
    setResult({ report_id: item.report_id, report: { summary: item.summary, period: item.period, progress: item.progress, risks: item.risks, plan: item.plan, data_sources: item.data_sources } })
    setPeriod(item.period)
    setNotice(item.published ? '这是已发布的周报，重新保存会更新内容。' : '')
  }

  const generated = result !== null
  const sourceList = sources.data?.sources ?? []
  const historyList = history.data?.reports ?? []
  return <main className="page-stack"><header className="page-heading"><div><span className="eyebrow">周报</span><h1>HR 周报</h1><p>生成的是可编辑草稿，不是未经核对即可发布的结论。</p></div></header><div className={styles.layout}><aside className={styles.controls}><label>报告周期<input value={period} onChange={e => setPeriod(e.target.value)} placeholder="2026-W33" /></label><section><strong>数据来源</strong>{sources.isPending && <p>正在读取可用的分析结果…</p>}{!sources.isPending && sourceList.length === 0 && <span>还没有可用的分析结果。请先在「面谈纪要」或「员工声音」中完成分析，再回到这里生成周报。</span>}{sourceList.length > 0 && <div className={styles.sourceList}>{sourceList.map(source => <label key={source.id} className={selected.includes(source.id) ? styles.sourceChecked : ''}><input type="checkbox" checked={selected.includes(source.id)} onChange={() => toggleSource(source.id)} /><span><b>{source.kind} · {source.created_at ? new Date(source.created_at).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'}</b><small>{source.label}</small></span></label>)}</div>}</section><button className="primary-button" onClick={generate} disabled={pending || !period || !selected.length}>{pending ? '正在生成…' : '生成周报'}</button>{notice && <p className={styles.notice}>{notice}</p>}{error && <p className={styles.error}>{error}</p>}<section className={styles.historySection}><strong>已保存的周报</strong>{history.isPending && <p>正在读取…</p>}{!history.isPending && historyList.length === 0 && <span>还没有保存过周报。</span>}{historyList.length > 0 && <div className={styles.historyList}>{historyList.map(item => <button key={item.report_id} className={result?.report_id === item.report_id ? styles.historyActive : ''} onClick={() => openHistory(item)}><span>{item.period || '（未填周期）'}</span><small>{item.published ? '已发布' : '草稿'} · {formatDate(item.created_at)}</small></button>)}</div>}</section></aside><article className={styles.paper}><div className={styles.paperHead}><div><span>HR 周报</span><h2>{period || '—'} 组织观察</h2></div>{generated && <div><button onClick={() => save('save')}>保存草稿</button><button className="primary-button" onClick={() => setConfirmPublish(true)}>发布</button></div>}</div>{!result && !pending && <AsyncState kind="empty" title="等待生成草稿" detail="选择左侧的数据来源后开始生成，或从下方打开已保存的周报。" />}{pending && <AsyncState kind="processing" title="正在汇总周报" detail="正在基于所选来源生成结构化进展、风险与计划。" />}{result && <div className={styles.reportEnter}><label className={styles.summary}>摘要<textarea rows={5} value={String(result.report.summary ?? '')} onChange={e => setResult({ ...result, report: { ...result.report, summary: e.target.value } })} /></label><ResultDocument result={Object.fromEntries(Object.entries(result.report).filter(([key]) => key !== 'summary'))} /></div>}</article></div><ConfirmDialog open={confirmPublish} title="发布周报" message="确认发布这份周报？发布后会记录发布时间。" confirmLabel="发布" onConfirm={() => save('publish')} onCancel={() => setConfirmPublish(false)} /></main>
}

function currentWeek() { const now = new Date(); const first = new Date(now.getFullYear(), 0, 1); const week = Math.ceil((((now.getTime() - first.getTime()) / 86400000) + first.getDay() + 1) / 7); return `${now.getFullYear()}-W${String(week).padStart(2, '0')}` }
function formatDate(value: string | null) { const date = value ? new Date(value) : null; return date && !Number.isNaN(date.getTime()) ? date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' }) : '—' }
