import { type FormEvent, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createKnowledgeBase, deleteDocument, deleteKnowledgeBase, listDocuments, listKnowledgeBases, triggerIngestion, uploadDocument, type KbDocument } from '../../api/knowledge-base'
import { AsyncState } from '../../components/AsyncState'
import { StatusBadge } from '../../components/StatusBadge'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import styles from './KnowledgeBasePage.module.css'
import { useSessionStore } from '../../app/session-store'
import { hasMinimumRole } from '../../app/roles'
import { PermissionNotice } from '../../components/PermissionNotice'

const statusLabels = { uploaded: '等待索引', parsing: '正在解析', indexed: '索引完成', error: '索引失败' }

export function KnowledgeBasePage() {
  const user = useSessionStore(state => state.user)
  if (!hasMinimumRole(user?.role, 'hr_manager')) {
    return <main className="page-stack"><header className="page-heading"><div><span className="eyebrow">KNOWLEDGE / INDEX</span><h1>知识库</h1><p>制度文件、索引任务与检索数据由 HR 经理或管理员维护。</p></div></header><PermissionNotice feature="知识库" requiredRole="HR 经理或管理员" /></main>
  }
  return <KnowledgeBaseManagerPage />
}

function KnowledgeBaseManagerPage() {
  const client = useQueryClient()
  const kbs = useQuery({ queryKey: ['kbs'], queryFn: listKnowledgeBases })
  const [selected, setSelected] = useState('')
  const current = selected || kbs.data?.[0]?.id || ''
  const docs = useQuery({ queryKey: ['kb-documents', current], queryFn: () => listDocuments(current), enabled: !!current, refetchInterval: query => query.state.data?.some(d => d.status === 'uploaded' || d.status === 'parsing') ? 2000 : false })
  const [name, setName] = useState('')
  const [notice, setNotice] = useState('')
  const [confirmTarget, setConfirmTarget] = useState<{ kind: 'kb' | 'doc'; kbName?: string; doc?: KbDocument } | null>(null)

  const refresh = () => {
    client.invalidateQueries({ queryKey: ['kbs'] })
    client.invalidateQueries({ queryKey: ['kb-documents', current] })
  }

  const create = useMutation({
    mutationFn: createKnowledgeBase,
    onSuccess: kb => {
      setSelected(kb.id)
      setName('')
      setNotice('知识库已创建，可以继续添加文件并开始索引。')
      refresh()
    },
  })
  const ingest = useMutation({ mutationFn: () => triggerIngestion(current), onSuccess: result => { setNotice(result.message ?? '索引任务已提交'); refresh() } })

  async function createKb(e: FormEvent) {
    e.preventDefault()
    if (name.trim()) create.mutate({ name: name.trim(), scenario_id: 'policy_qa' })
  }

  async function confirmDeleteKb() {
    const kb = kbs.data?.find(k => k.id === current)
    if (!kb) return
    await deleteKnowledgeBase(current)
    setConfirmTarget(null)
    setSelected('')
    refresh()
  }

  async function confirmDeleteDoc() {
    const doc = confirmTarget?.doc
    if (!doc) return
    await deleteDocument(current, doc.id)
    setConfirmTarget(null)
    refresh()
  }

  return <main className="page-stack"><header className="page-heading"><div><span className="eyebrow">KNOWLEDGE / INDEX</span><h1>知识库</h1><p>文件上传与索引是两个独立步骤；只有“索引完成”的文件能参与检索。</p></div></header>
    <div className={styles.layout}><aside className={styles.directory}><form onSubmit={createKb}><label htmlFor="kb-name">新知识库</label><div><input id="kb-name" value={name} onChange={e => setName(e.target.value)} placeholder="例如：员工制度 2026" /><button aria-label="创建知识库">＋</button></div>{create.error && <p className={styles.error}>{create.error.message}</p>}</form><h2>目录</h2>{kbs.isPending && <p>正在读取…</p>}{kbs.data?.length === 0 && <p className={styles.muted}>还没有知识库。</p>}{kbs.data?.map(kb => <button key={kb.id} className={current === kb.id ? styles.active : ''} onClick={() => setSelected(kb.id)}><span>{kb.name}</span><small>{kb.document_count ?? 0} 份文件</small></button>)}</aside>
      <section className={styles.detail}>{!current ? <AsyncState kind="empty" title="先创建知识库" detail="制度问答需要至少一个已索引的知识库。" /> : <><div className={styles.detailHead}><div><span className="eyebrow">SELECTED LIBRARY</span><h2>{kbs.data?.find(k => k.id === current)?.name}</h2></div><button className={styles.danger} onClick={() => setConfirmTarget({ kind: 'kb', kbName: kbs.data?.find(k => k.id === current)?.name })}>删除知识库</button></div><DocumentUploader kbId={current} onUploaded={refresh} /><div className={styles.indexBar}><div><strong>文档索引</strong><small>等待索引或失败的文件会在启动后进入解析队列。</small></div><button className="primary-button" onClick={() => ingest.mutate()} disabled={ingest.isPending || !docs.data?.some(d => d.status === 'uploaded' || d.status === 'error')}>开始索引</button></div>{notice && <p className={styles.notice}>{notice}</p>}{docs.isPending ? <AsyncState kind="loading" title="正在读取文档" /> : <DocumentTable documents={docs.data ?? []} onDelete={doc => setConfirmTarget({ kind: 'doc', doc })} />}</>}</section>
    </div>
    <ConfirmDialog open={confirmTarget !== null} title={confirmTarget?.kind === 'kb' ? '删除知识库' : '删除文件'} message={confirmTarget?.kind === 'kb' ? `确认删除知识库“${confirmTarget?.kbName ?? ''}”及其全部文件？此操作不可恢复。` : `确认删除文件“${confirmTarget?.doc?.filename ?? ''}”？此操作不可恢复。`} confirmLabel="删除" danger onConfirm={confirmTarget?.kind === 'kb' ? confirmDeleteKb : confirmDeleteDoc} onCancel={() => setConfirmTarget(null)} />
  </main>
}

export function DocumentUploader({ kbId, onUploaded }: { kbId: string; onUploaded?: () => void }) {
  const [error, setError] = useState('')
  const upload = useMutation({ mutationFn: (file: File) => uploadDocument(kbId, file), onSuccess: () => { setError(''); onUploaded?.() } })

  async function pick(file?: File) {
    if (!file) return
    const ext = file.name.split('.').pop()?.toLowerCase()
    if (!['txt', 'pdf', 'docx'].includes(ext ?? '')) return setError('仅支持 TXT、PDF、DOCX')
    if (file.size > 20 * 1024 * 1024) return setError('文件不能超过 20 MB')
    setError('')
    upload.mutate(file)
  }

  return <div className={styles.uploader}><div><strong>添加制度文件</strong><p>支持 TXT、PDF、DOCX，单文件不超过 20 MB。</p></div><label className="secondary-button">上传制度文件<input type="file" accept=".txt,.pdf,.docx" hidden onChange={e => pick(e.target.files?.[0])} /></label>{(error || upload.error) && <p role="alert" className={styles.error}>{error || upload.error?.message}</p>}{upload.isPending && <p>正在上传…</p>}</div>
}

export function DocumentTable({ documents, onDelete }: { documents: KbDocument[]; onDelete?: (doc: KbDocument) => void }) {
  if (!documents.length) return <AsyncState kind="empty" title="还没有文档" detail="上传后需要手动开始索引。" />
  return <div className={styles.tableWrap}><table><thead><tr><th>文件</th><th>状态</th><th>切片</th><th>大小</th>{onDelete && <th />}</tr></thead><tbody>{documents.map(doc => <tr key={doc.id}><td><strong>{doc.filename}</strong>{doc.error_message && <small className={styles.error}>{doc.error_message}</small>}</td><td><StatusBadge status={doc.status} label={statusLabels[doc.status]} /></td><td>{doc.chunk_count}</td><td>{formatBytes(doc.size_bytes)}</td>{onDelete && <td><button onClick={() => onDelete(doc)}>删除</button></td>}</tr>)}</tbody></table></div>
}

function formatBytes(value: number) { if (value < 1024) return `${value} B`; if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`; return `${(value / 1024 / 1024).toFixed(1)} MB` }
