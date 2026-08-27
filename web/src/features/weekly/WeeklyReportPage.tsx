import { useState } from 'react'
import { generateWeeklyReport, saveWeeklyReport } from '../../api/content-workflows'
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

  if (!hasMinimumRole(user?.role, 'hrbp')) {
    return <main className="page-stack"><header className="page-heading"><h1>HR 周报</h1></header><PermissionNotice feature="HR 周报" /></main>
  }

  async function generate() {
    setPending(true)
    setError('')
    setNotice('')
    try {
      const response = await generateWeeklyReport({ period, source_ids: [], draft_mode: true })
      setResult(response)
      setNotice('草稿已生成，可以先检查再保存。')
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
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败')
    }
  }

  const generated = result !== null
  return <main className="page-stack"><header className="page-heading"><div><span className="eyebrow">WEEKLY / EDITORIAL PAPER</span><h1>HR 周报</h1><p>生成的是可编辑草稿，不是未经核对即可发布的结论。</p></div></header><div className={styles.layout}><aside className={styles.controls}><label>报告周期<input value={period} onChange={e => setPeriod(e.target.value)} placeholder="2026-W33" /></label><section><strong>数据来源</strong><p>当前未选择可持久化来源。</p><span>请先上传面谈纪要或员工声音数据，再生成真实周报。</span></section><button className="primary-button" onClick={generate} disabled={pending || !period}>{pending ? '正在生成…' : '生成周报'}</button>{notice && <p className={styles.notice}>{notice}</p>}{error && <p className={styles.error}>{error}</p>}<small>周报和历史会同步写入后端，你可以随时重新打开查看。</small></aside><article className={styles.paper}><div className={styles.paperHead}><div><span>HRBPILOT / WEEKLY</span><h2>{period} 组织观察</h2></div>{generated && <div><button onClick={() => save('save')}>保存草稿</button><button className="primary-button" onClick={() => setConfirmPublish(true)}>发布</button></div>}</div>{!result && !pending && <AsyncState kind="empty" title="等待生成草稿" detail="先上传并持久化数据来源，再生成周报。" />}{pending && <AsyncState kind="processing" title="正在汇总周报" detail="后端正在生成结构化进展、风险与计划。" />}{result && <div className={styles.reportEnter}><label className={styles.summary}>摘要<textarea rows={5} value={String(result.report.summary ?? '')} onChange={e => setResult({ ...result, report: { ...result.report, summary: e.target.value } })} /></label><ResultDocument result={Object.fromEntries(Object.entries(result.report).filter(([key]) => key !== 'summary'))} /></div>}</article></div><ConfirmDialog open={confirmPublish} title="发布周报" message="确认发布这份周报？发布动作会被后端记录。" confirmLabel="发布" onConfirm={() => save('publish')} onCancel={() => setConfirmPublish(false)} /></main>
}

function currentWeek() { const now = new Date(); const first = new Date(now.getFullYear(), 0, 1); const week = Math.ceil((((now.getTime() - first.getTime()) / 86400000) + first.getDay() + 1) / 7); return `${now.getFullYear()}-W${String(week).padStart(2, '0')}` }
