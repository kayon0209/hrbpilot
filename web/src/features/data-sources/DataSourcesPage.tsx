import { type FormEvent, useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AsyncState } from '../../components/AsyncState'
import { listAdminUsers, type AdminUserView } from '../../api/admin-users'
import { bindPlatformIdentity, configureWeComCallback, createDataSource, listDataSources, pauseDataSource, resumeDataSource, revokeDataSource, PLATFORMS, type DataSourceView } from '../../api/data-sources'
import styles from './DataSourcesPage.module.css'

/**
 * 数据接入 (spec §7.10) — admin manages external channels in business
 * language. Every entry answers 接了什么 / 取了什么 / 去了哪里 / 如何撤销.
 * No connector vocabulary; credentials never render.
 */
export function DataSourcesPage() {
  const queryClient = useQueryClient()
  const sources = useQuery({ queryKey: ['data-sources'], queryFn: listDataSources })
  const users = useQuery({ queryKey: ['admin-users'], queryFn: listAdminUsers })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['data-sources'] })
  const pause = useMutation({ mutationFn: pauseDataSource, onSuccess: invalidate })
  const resume = useMutation({ mutationFn: resumeDataSource, onSuccess: invalidate })
  const revoke = useMutation({ mutationFn: ({ id, reason }: { id: string; reason: string }) => revokeDataSource(id, reason), onSuccess: invalidate })
  const create = useMutation({ mutationFn: createDataSource, onSuccess: invalidate })
  const bindIdentity = useMutation({
    mutationFn: ({ sourceId, externalUserId, userId }: { sourceId: string; externalUserId: string; userId: string }) => (
      bindPlatformIdentity(sourceId, { external_user_id: externalUserId, user_id: userId })
    ),
  })
  const configureWeCom = useMutation({
    mutationFn: ({ sourceId, body }: { sourceId: string; body: Parameters<typeof configureWeComCallback>[1] }) => configureWeComCallback(sourceId, body),
    onSuccess: invalidate,
  })

  const [name, setName] = useState('')
  const [platform, setPlatform] = useState('feishu')
  const [purpose, setPurpose] = useState('')
  const [scope, setScope] = useState('')
  const [chatIds, setChatIds] = useState('')
  const [eventRoute, setEventRoute] = useState<'none' | 'employee_request'>('none')
  const [destination, setDestination] = useState('')
  const [showCreate, setShowCreate] = useState(true)
  const [revokingId, setRevokingId] = useState<string | null>(null)
  const [revokeReason, setRevokeReason] = useState('')

  useEffect(() => {
    if ((sources.data?.sources.length ?? 0) > 0 && !create.isSuccess) setShowCreate(false)
  }, [create.isSuccess, sources.data?.sources.length])

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!name.trim() || !purpose.trim() || !scope.trim() || !destination.trim()) return
    create.reset()
    await create.mutateAsync({
      name: name.trim(),
      platform,
      purpose: purpose.trim(),
      authorized_scope: scope.trim(),
      authorized_scope_json: platform === 'wecom' && chatIds.trim()
        ? { chat_ids: chatIds.split(',').map(value => value.trim()).filter(Boolean), folder_ids: [] }
        : undefined,
      event_route: eventRoute,
      content_types: eventRoute === 'employee_request' ? ['messages'] : ['documents', 'attachments'],
      data_destination: destination.trim(),
    })
    setName(''); setPurpose(''); setScope(''); setChatIds(''); setEventRoute('none'); setDestination(''); setShowCreate(false)
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

      {showCreate && <section className="panel">
        <h2>登记材料来源</h2>
        <p className={styles.note}>先记录要接入什么和谁可以使用。账号授权将在安全接入流程开放后单独完成，本页不会收集密码或密钥。</p>
        <form onSubmit={submit} className={styles.form}>
          <label>名称<input value={name} onChange={e => setName(e.target.value)} maxLength={200} required placeholder="例如：飞书制度文档" /></label>
          <label>来源平台
            <select value={platform} onChange={e => {
              const next = e.target.value
              setPlatform(next)
              if (next !== 'wecom' && next !== 'feishu') setEventRoute('none')
            }}>
              {PLATFORMS.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
          </label>
          <label>用途<input value={purpose} onChange={e => setPurpose(e.target.value)} maxLength={2000} required placeholder="接入后材料用于什么工作" /></label>
          <label>入站处理
            <select value={eventRoute} onChange={e => setEventRoute(e.target.value as 'none' | 'employee_request')}>
              <option value="none">仅同步授权材料，不自动创建工作</option>
              <option value="employee_request" disabled={platform !== 'wecom' && platform !== 'feishu'}>把员工消息登记为员工请求</option>
            </select>
            {eventRoute === 'employee_request' && <small>仅在平台账号已与员工身份显式绑定后，消息才会生成员工请求；未绑定消息会等待管理员确认，绝不按昵称猜测。</small>}
          </label>
          <label>授权范围<input value={scope} onChange={e => setScope(e.target.value)} maxLength={2000} required placeholder="仅授权哪些文件夹、群组或规则" /></label>
          {platform === 'wecom' && <label>企业微信群 ID（可选，逗号分隔）<input value={chatIds} onChange={e => setChatIds(e.target.value)} maxLength={2000} placeholder="chat_hr, chat_east" /><small>自建应用可直接接收员工私聊，不需要群 ID；如另行启用群消息，再在这里记录经授权的群范围。</small></label>}
          <label>数据去向<input value={destination} onChange={e => setDestination(e.target.value)} maxLength={2000} required placeholder="材料进入哪个工作区，谁可见" /></label>
          <p className={styles.note}>完成企业授权后，本页会显示实际可同步范围。个人微信不做任何聊天抓取。</p>
          <div className={styles.actions}>
            <button className="primary-button" type="submit" disabled={create.isPending}>{create.isPending ? '正在保存…' : '保存接入计划'}</button>
            {(sources.data?.sources.length ?? 0) > 0 && <button className="secondary-button" type="button" onClick={() => setShowCreate(false)}>取消新增</button>}
          </div>
          {create.isError && <p className={styles.error} role="alert">添加未保存：{create.error.message}</p>}
        </form>
      </section>}

      <section className="panel" aria-labelledby="list-heading">
        <div className={styles.sectionHeading}>
          <h2 id="list-heading">接入计划</h2>
          {!showCreate && <button className="secondary-button" type="button" onClick={() => setShowCreate(true)}>新增接入</button>}
        </div>
        {sources.isPending && <AsyncState kind="loading" title="正在读取接入" />}
        {sources.isError && (
          <AsyncState kind="error" title="接入读取失败" detail={sources.error.message} action={<button onClick={() => sources.refetch()}>重试</button>} />
        )}
        {sources.data && sources.data.sources.length === 0 && (
          <p>还没有登记接入计划。HR 也可以直接在各项工作里上传本地文件。</p>
        )}
        <div className={styles.list}>
          {(sources.data?.sources ?? []).map(source => (
            <SourceCard
              key={source.source_id}
              source={source}
              employees={(users.data?.users ?? []).filter(user => user.role === 'employee')}
              employeesLoading={users.isPending}
              bindingError={bindIdentity.isError ? bindIdentity.error.message : null}
              onBindIdentity={(externalUserId, userId) => bindIdentity.mutateAsync({ sourceId: source.source_id, externalUserId, userId })}
              wecomConfigError={configureWeCom.isError ? configureWeCom.error.message : null}
              onConfigureWeCom={body => configureWeCom.mutateAsync({ sourceId: source.source_id, body })}
              onPause={pause.mutate}
              onResume={resume.mutate}
              onRevoke={setRevokingId}
              busy={pause.isPending || resume.isPending || revoke.isPending || bindIdentity.isPending || configureWeCom.isPending}
            />
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

function SourceCard({ source, employees, employeesLoading, bindingError, onBindIdentity, wecomConfigError, onConfigureWeCom, onPause, onResume, onRevoke, busy }: {
  source: DataSourceView
  employees: AdminUserView[]
  employeesLoading: boolean
  bindingError: string | null
  onBindIdentity: (externalUserId: string, userId: string) => Promise<unknown>
  wecomConfigError: string | null
  onConfigureWeCom: (body: Parameters<typeof configureWeComCallback>[1]) => Promise<unknown>
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
        <div><dt>入站处理</dt><dd>{source.event_route === 'employee_request' ? '员工消息登记为员工请求' : '仅同步授权材料'}</dd></div>
        <div><dt>授权范围</dt><dd>{source.authorized_scope}</dd></div>
        <div><dt>数据去向</dt><dd>{source.data_destination}</dd></div>
        <div><dt>上次同步</dt><dd>{source.last_sync_at ? new Date(source.last_sync_at).toLocaleString('zh-CN') : '尚未同步'}</dd></div>
      </dl>
      {source.last_error && <p className={styles.errorNote}>最近一次同步失败：{source.last_error}</p>}
      {revoked ? (
        <p className={styles.revokedNote}>已于 {source.revoked_at?.slice(0, 10)} 撤销：{source.revoked_reason}。如需再次使用，请新建接入并重新授权。</p>
      ) : (
        <>
          {source.platform === 'wecom' && source.event_route === 'employee_request' && <WeComCallbackForm
            configured={source.wecom_callback_configured}
            corpId={source.wecom_corp_id}
            agentId={source.wecom_agent_id}
            callbackPath={source.wecom_callback_path}
            error={wecomConfigError}
            busy={busy}
            onConfigure={onConfigureWeCom}
          />}
          {source.event_route === 'employee_request' && <IdentityBindingForm
            employees={employees}
            employeesLoading={employeesLoading}
            bindingError={bindingError}
            busy={busy}
            onBind={onBindIdentity}
          />}
          <div className={styles.cardActions}>
            {source.paused ? (
              <button className="secondary-button" disabled={busy} onClick={() => onResume(source.source_id)}>恢复同步</button>
            ) : (
              <button className="secondary-button" disabled={busy} onClick={() => onPause(source.source_id)}>暂停同步</button>
            )}
            <button className="secondary-button" disabled={busy} onClick={() => onRevoke(source.source_id)}>撤销授权</button>
          </div>
        </>
      )}
    </article>
  )
}

function WeComCallbackForm({ configured, corpId, agentId, callbackPath, error, busy, onConfigure }: {
  configured: boolean
  corpId: string | null
  agentId: string | null
  callbackPath: string | null
  error: string | null
  busy: boolean
  onConfigure: (body: Parameters<typeof configureWeComCallback>[1]) => Promise<unknown>
}) {
  const [corpIdInput, setCorpIdInput] = useState(corpId ?? '')
  const [agentIdInput, setAgentIdInput] = useState(agentId ?? '')
  const [corpSecret, setCorpSecret] = useState('')
  const [callbackToken, setCallbackToken] = useState('')
  const [encodingAesKey, setEncodingAesKey] = useState('')
  const [notice, setNotice] = useState('')
  const [copyNotice, setCopyNotice] = useState('')

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!corpIdInput.trim() || !agentIdInput.trim() || !corpSecret || !callbackToken || !encodingAesKey) return
    setNotice('')
    await onConfigure({
      corp_id: corpIdInput.trim(), agent_id: agentIdInput.trim(), corp_secret: corpSecret,
      callback_token: callbackToken, encoding_aes_key: encodingAesKey,
    })
    setCorpSecret(''); setCallbackToken(''); setEncodingAesKey('')
    setNotice('已加密保存回调配置。请在企业微信自建应用中填入本数据源的回调地址并完成 URL 验证。')
  }

  return <section className={styles.identityBinding} aria-label="配置企业微信回调">
    <h2>第 1 步：企业微信回调</h2>
    <p>{configured ? `已配置 CorpID ${corpId}、AgentID ${agentId}；如需轮换密钥，请完整重新填写。` : '填写自建应用的回调参数。密钥只会加密保存，保存后不会再次显示。'}</p>
    {callbackPath && <div className={styles.callbackPath}>
      <p>在企业微信应用回调 URL 中填写“已部署服务的 HTTPS 域名”加下方路径：</p>
      <code>{callbackPath}</code>
      <button className="secondary-button" type="button" onClick={async () => {
        try {
          await navigator.clipboard.writeText(callbackPath)
          setCopyNotice('回调路径已复制；请粘贴在企业微信应用的 HTTPS 域名之后。')
        } catch {
          setCopyNotice('浏览器未允许复制，请手动复制上方路径。')
        }
      }}>复制回调路径</button>
      {copyNotice && <p role="status">{copyNotice}</p>}
    </div>}
    <form onSubmit={submit} className={styles.form}>
      <label>CorpID<input value={corpIdInput} onChange={event => setCorpIdInput(event.target.value)} maxLength={128} required /></label>
      <label>AgentID<input value={agentIdInput} onChange={event => setAgentIdInput(event.target.value)} inputMode="numeric" maxLength={32} required /></label>
      <label>Secret<input type="password" value={corpSecret} onChange={event => setCorpSecret(event.target.value)} maxLength={2000} required /></label>
      <label>回调 Token<input type="password" value={callbackToken} onChange={event => setCallbackToken(event.target.value)} maxLength={32} required /></label>
      <label>EncodingAESKey<input type="password" value={encodingAesKey} onChange={event => setEncodingAesKey(event.target.value)} minLength={43} maxLength={43} required /></label>
      <div className={styles.actions}><button className="secondary-button" type="submit" disabled={busy}>{busy ? '正在加密保存…' : configured ? '更新回调配置' : '保存回调配置'}</button></div>
      {notice && <p role="status">{notice}</p>}
      {error && <p className={styles.error} role="alert">配置未保存：{error}</p>}
    </form>
  </section>
}

function IdentityBindingForm({ employees, employeesLoading, bindingError, busy, onBind }: {
  employees: AdminUserView[]
  employeesLoading: boolean
  bindingError: string | null
  busy: boolean
  onBind: (externalUserId: string, userId: string) => Promise<unknown>
}) {
  const [externalUserId, setExternalUserId] = useState('')
  const [userId, setUserId] = useState('')
  const [notice, setNotice] = useState('')

  async function submit(event: FormEvent) {
    event.preventDefault()
    const external = externalUserId.trim()
    if (!external || !userId) return
    setNotice('')
    await onBind(external, userId)
    setExternalUserId('')
    setUserId('')
    setNotice('账号已绑定；同一账号此前等待确认的消息将登记为该员工的请求。')
  }

  return <section className={styles.identityBinding} aria-label="确认平台员工身份">
    <h2>第 2 步：确认平台员工身份</h2>
    <p>URL 验证后，核验实际账号归属再操作。系统不会按昵称、姓名或邮箱自动匹配。</p>
    {employeesLoading ? <p>正在读取员工名单…</p> : employees.length === 0 ? <p>尚无可绑定员工，请先在“用户与权限”中建立员工账号。</p> : <form onSubmit={submit} className={styles.form}>
      <label>平台账号 ID<input value={externalUserId} onChange={event => setExternalUserId(event.target.value)} maxLength={200} required placeholder="例如：ou_123 或 userid" /></label>
      <label>HRBPilot 员工
        <select value={userId} onChange={event => setUserId(event.target.value)} required>
          <option value="">请选择已核验的员工</option>
          {employees.map(employee => <option key={employee.user_id} value={employee.user_id}>{employee.name}（{employee.email}）</option>)}
        </select>
      </label>
      <div className={styles.actions}><button className="secondary-button" type="submit" disabled={busy}>{busy ? '正在确认…' : '确认并绑定'}</button></div>
      {notice && <p role="status">{notice}</p>}
      {bindingError && <p className={styles.error} role="alert">绑定失败：{bindingError}</p>}
    </form>}
  </section>
}
