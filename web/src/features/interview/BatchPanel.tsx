import { useEffect, useState } from 'react'
import { createMaterialBatch, getMaterialBatch, retryMaterialBatch } from '../../api/async-scenarios'

type Props = { type: 'interview' | 'voice' }

export function BatchPanel({ type }: Props) {
  const [text, setText] = useState('')
  const [batchId, setBatchId] = useState<string | null>(null)
  const [info, setInfo] = useState<{ total: number; completed: number; failed: number; status: string } | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  // lines = one item per line: "员工名|原文" (名前留空则匿名), 至少50字/条，>500 截断
  async function submit() {
    const lines = text.split('\n').map(s => s.trim()).filter(Boolean)
    if (lines.length === 0) return setError('先粘贴批量内容，每行一条')
    const items = lines.slice(0, 500).map(line => {
      const bar = line.indexOf('|')
      if (bar >= 0) return { content: line.slice(bar + 1).trim(), employee_name: line.slice(0, bar).trim() }
      return { content: line }
    }).filter(i => i.content.length >= 50)
    if (items.length === 0) return setError('有效条目需每条≥50字，可用"姓名|内容"格式')
    setBusy(true)
    setError('')
    try {
      const row = await createMaterialBatch({ type, items })
      setBatchId(row.batch_id)
      setInfo({ total: row.total, completed: row.completed, failed: row.failed, status: row.status })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    if (!batchId) return
    let cancelled = false
    let timer: number
    async function poll() {
      try {
        const row = await getMaterialBatch(batchId!)
        if (!cancelled) setInfo({ total: row.total, completed: row.completed, failed: row.failed, status: row.status })
        if (row.status !== 'completed') timer = window.setTimeout(poll, 2500)
      } catch { /* keep previous info */ if (!cancelled) timer = window.setTimeout(poll, 4000) }
    }
    poll()
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [batchId])

  async function retry() {
    if (!batchId) return
    setBusy(true)
    try { await retryMaterialBatch(batchId); const row = await getMaterialBatch(batchId); setInfo({ total: row.total, completed: row.completed, failed: row.failed, status: row.status }) } finally { setBusy(false) }
  }

  return (
    <section className="panel" style={{ marginTop: 16 }}>
      <h3 style={{ margin: 0 }}>批量导入（{type === 'interview' ? '面谈纪要' : '员工声音'}，每行一条）</h3>
      <p className="form-note">用于 500 条级问卷：每行一条，至少 50 字；用 <code>姓名|内容</code> 可标员工，留空则匿名。失败单条可重试。</p>
      <textarea value={text} onChange={e => setText(e.target.value)} rows={6} placeholder="例：张三|这次绩效沟通主要反馈..." style={{ width: '100%', marginTop: 8 }} />
      <div style={{ display: 'flex', gap: 10, marginTop: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <button type="button" className="primary-button" onClick={submit} disabled={busy}>开始批量</button>
        {info && <span style={{ fontSize: 13 }}>{info.completed + info.failed}/{info.total}（成功 {info.completed} · 失败 {info.failed}）— {info.status === 'completed' ? '已完成' : '分析中'}</span>}
        {info && info.failed > 0 && <button type="button" className="secondary-button" onClick={retry} disabled={busy}>重试失败 {info.failed} 条</button>}
        {batchId && <span style={{ fontSize: 12, color: 'var(--muted)' }}>{batchId}</span>}
      </div>
      {error && <p role="alert" style={{ color: 'crimson' }}>{error}</p>}
    </section>
  )
}
