import { type FormEvent, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AsyncState } from '../../components/AsyncState'
import { createDataSource, listDataSources, pauseDataSource, resumeDataSource, revokeDataSource, PLATFORMS, type DataSourceView } from '../../api/data-sources'
import styles from './DataSourcesPage.module.css'

/**
 * 数据接入 (spec §7.10) — admin manages external channels in business
 * language. Every entry answers 接了什么 / 取了什么 / 去了哪里 / 如何撤销.
 * No connector vocabulary; credentials never render.
 */
export function DataSourcesPage() {
  const queryClient = useQueryClient()
  const sources = useQuery({ queryKey: ['data-sources'], queryFn: listDataSources })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['data-sources'] })
  const pause = useMutation({ mutationFn: pauseDataSource, onSuccess: invalidate })
  const resume = useMutation({ mutationFn: resumeDataSource, onSuccess: invalidate })
  const revoke = useMutation({ mutationFn: ({ id, reason }: { id: string; reason: string }) => revokeDataSource(id, reason), onSuccess: invalidate })
  const create = useMutation({ mutationFn: createDataSource, onSuccess: invalidate })

  const [name, setName] = useState('')
  const [platform, setPlatform] = useState('feishu')
  const [purpose, setPurpose] = useState('')
  const [scope, setScope] = useState('')
  const [destination, setDestination] = useState('')
  const [revokingId, setRevokingId] = useState<string | null>(null)
  const [revokeReason, setRevokeReason] = useState('')

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!name.trim() || !purpose.trim() || !scope.trim() || !destination.trim()) return
    create.reset()
    await create.mutateAsync({
      name: name.trim(),
      platform,
      purpose: purpose.trim(),
      authorized_scope: scope.trim(),
      content_types: ['documents', 'attachments'],
      data_destination: destination.trim(),
    })
    setName(''); setPurpose(''); setScope(''); setDestination('')
  }

  return (
    <main className="page-stack">
      <header className="page-heading">
        <div>
          <span className="eyebrow">管理后台</span>
          <h1>数据接入</h1>
          <p>管理组织外部材料的进入渠道。每项接入都标明用途、授权范围与数据去向；撤销立即停止新同步。</p>
        </div>
      </header>

      <section className="panel">
        <h2>登记材料来源</h2>
        <p className={styles.note}>先记录要接入什么和谁可以使用。账号授权将在安全接入流程开放后单独完成，本页不会收集密码或密钥。</p>
        <form onSubmit={submit} className={styles.form}>
          <label>名称<input value={name} onChange={e => setName(e.target.value)} maxLength={200} required placeholder="例如：飞书制度文档" /></label>
          <label>来源平台
            <select value={platform} onChange={e => setPlatform(e.target.value)}>
              {PLATFORMS.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
          </label>
          <label>用途<input value={purpose} onChange={e => setPurpose(e.target.value)} maxLength={2000} required placeholder="接入后材料用于什么工作" /></label>
          <label>授权范围<input value={scope} onChange={e => setScope(e.target.value)} maxLength={2000} required placeholder="仅授权哪些文件夹、群组或规则" /></label>
          <label>数据去向<input value={destination} onChange={e => setDestination(e.target.value)} maxLength={2000} required placeholder="材料进入哪个工作区，谁可见" /></label>
          <p className={styles.note}>完成企业授权后，本页会显示实际可同步范围。个人微信不做任何聊天抓取。</p>
          <div className={styles.actions}>
            <button className="primary-button" type="submit" disabled={create.isPending}>{create.isPending ? '正在保存…' : '保存接入计划'}</button>
          </div>
          {create.isError && <p className={styles.error} role="alert">添加未保存：{create.error.message}</p>}
        </form>
      </section>

      <section className="panel" aria-labelledby="list-heading">
        <h2 id="list-heading">接入计划</h2>
        {sources.isPending && <AsyncState kind="loading" title="正在读取接入" />}
        {sources.isError && (
          <AsyncState kind="error" title="接入读取失败" detail={sources.error.message} action={<button onClick={() => sources.refetch()}>重试</button>} />
        )}
        {sources.data && sources.data.sources.length === 0 && (
          <p>还没有登记接入计划。HR 也可以直接在各项工作里上传本地文件。</p>
        )}
        <div className={styles.list}>
          {(sources.data?.sources ?? []).map(source => (
            <SourceCard key={source.source_id} source={source} onPause={pause.mutate} onResume={resume.mutate} onRevoke={setRevokingId} busy={pause.isPending || resume.isPending || revoke.isPending} />
          ))}
        </div>
      </section>
      {revokingId && <div className={styles.dialogBackdrop} role="presentation">
        <section className={styles.dialog} role="dialog" aria-modal="true" aria-labelledby="revoke-heading">
          <h2 id="revoke-heading">撤销材料授权</h2>
          <p>撤销后会立即停止新同步，已保存的撤销记录不会被删除。</p>
          <label>撤销原因<textarea autoFocus rows={3} value={revokeReason} onChange={event => setRevokeReason(event.target.value)} maxLength={1000} placeholder="例如：授权范围已调整" /></label>
          <div className={styles.actions}>
            <button className="secondary-button" type="button" onClick={() => { setRevokingId(null); setRevokeReason('') }}>取消</button>
            <button className="primary-button" type="button" disabled={!revokeReason.trim() || revoke.isPending} onClick={async () => {
              await revoke.mutateAsync({ id: revokingId, reason: revokeReason.trim() })
              setRevokingId(null); setRevokeReason('')
            }}>{revoke.isPending ? '正在撤销…' : '确认撤销'}</button>
          </div>
        </section>
      </div>}
    </main>
  )
}

function SourceCard({ source, onPause, onResume, onRevoke, busy }: {
  source: DataSourceView
  onPause: (id: string) => void
  onResume: (id: string) => void
  onRevoke: (id: string) => void
  busy: boolean
}) {
  const revoked = !!source.revoked_at
  const certLabel = source.certification_label ?? '准备接入'
  // Only level-4 certified channels actually move data (spec §10.3); anything
  // below is a registered plan whose "sync" controls only gate future runs.
  const operational = source.certification_level >= 4
  return (
    <article className={`${styles.card} ${revoked ? styles.cardRevoked : ''}`}>
      <div className={styles.cardHead}>
        <strong>{source.platform_label} · {source.name}</strong>
        <span className={`${styles.sync} ${styles[`sync_${source.sync_status}`] ?? ''}`}>{source.sync_label}</span>
      </div>
      <dl className={styles.meta}>
        <div><dt>认证状态</dt><dd>{certLabel}{operational ? '' : '（尚未完成企业授权，暂不读取任何数据）'}</dd></div>
        <div><dt>用途</dt><dd>{source.purpose}</dd></div>
        <div><dt>授权范围</dt><dd>{source.authorized_scope}</dd></div>
        <div><dt>数据去向</dt><dd>{source.data_destination}</dd></div>
        <div><dt>上次同步</dt><dd>{source.last_sync_at ? new Date(source.last_sync_at).toLocaleString('zh-CN') : '尚未同步'}</dd></div>
      </dl>
      {source.last_error && <p className={styles.errorNote}>最近一次同步失败：{source.last_error}</p>}
      {revoked ? (
        <p className={styles.revokedNote}>已于 {source.revoked_at?.slice(0, 10)} 撤销：{source.revoked_reason}。如需再次使用，请新建接入并重新授权。</p>
      ) : (
        <div className={styles.cardActions}>
          {source.paused ? (
            <button className="secondary-button" disabled={busy} onClick={() => onResume(source.source_id)}>恢复同步</button>
          ) : (
            <button className="secondary-button" disabled={busy} onClick={() => onPause(source.source_id)}>暂停同步</button>
          )}
          <button className="secondary-button" disabled={busy} onClick={() => onRevoke(source.source_id)}>撤销授权</button>
        </div>
      )}
    </article>
  )
}
