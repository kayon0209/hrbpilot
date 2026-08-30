import { type FormEvent, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { AsyncState } from '../../components/AsyncState'
import { createMyRequest, listMyRequests, REQUEST_TYPES } from '../../api/my-requests'
import styles from './MyRequestsPage.module.css'

/**
 * 我的请求 (spec §7.9) — the employee's service contract.
 *
 * Employee sees only desensitized business status (已提交/待补充/处理中/已解决),
 * the next step and what materials are missing. Internal notes, risk levels
 * and HRCase links never appear here.
 */
export function MyRequestsPage() {
  const queryClient = useQueryClient()
  const mine = useQuery({ queryKey: ['my-requests'], queryFn: listMyRequests })
  const create = useMutation({
    mutationFn: createMyRequest,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['my-requests'] }),
  })
  const [requestType, setRequestType] = useState<string>('policy_check')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!title.trim() || !description.trim()) return
    create.reset()
    await create.mutateAsync({ request_type: requestType, title: title.trim(), description: description.trim() })
    setTitle('')
    setDescription('')
  }

  const requests = mine.data?.requests ?? []

  return (
    <main className="page-stack">
      <header className="page-heading">
        <div>
          <span className="eyebrow">员工服务</span>
          <h1>我的请求</h1>
          <p>制度问题先到「问 HR」查询；无法通过制度解答的个人情形，在这里提交请求并跟踪处理。</p>
        </div>
      </header>

      <section className="panel">
        <h2>提交新请求</h2>
        <form onSubmit={submit} className={styles.form}>
          <label>请求类型
            <select value={requestType} onChange={e => setRequestType(e.target.value)}>
              {REQUEST_TYPES.map(type => <option key={type.value} value={type.value}>{type.label}</option>)}
            </select>
          </label>
          <small className={styles.hint}>{REQUEST_TYPES.find(t => t.value === requestType)?.hint}</small>
          <label>标题<input value={title} onChange={e => setTitle(e.target.value)} maxLength={200} required placeholder="一句话说明你要办的事" /></label>
          <label>具体情况<textarea rows={4} value={description} onChange={e => setDescription(e.target.value)} maxLength={4000} required placeholder="说明背景和你需要的帮助" /></label>
          <div className={styles.actions}>
            <button className="primary-button" type="submit" disabled={create.isPending}>{create.isPending ? '正在提交…' : '提交请求'}</button>
          </div>
          {create.isError && <p className={styles.error} role="alert">提交未保存：{create.error.message}</p>}
          {create.isSuccess && <p className={styles.ok} role="status">请求已提交。HR 会尽快查看，需要补充材料时会在这里说明。</p>}
        </form>
      </section>

      <section className="panel" aria-labelledby="mine-heading">
        <h2 id="mine-heading">我的请求记录</h2>
        {mine.isPending && <AsyncState kind="loading" title="正在读取请求" />}
        {mine.isError && (
          <AsyncState kind="error" title="请求读取失败" detail={mine.error.message} action={<button onClick={() => mine.refetch()}>重试</button>} />
        )}
        {mine.data && requests.length === 0 && (
          <div>
            <p>还没有提交过请求。一般制度问题可以在<Link to="/policy">问 HR</Link>中直接得到答案。</p>
          </div>
        )}
        {requests.length > 0 && (
          <div className={styles.list}>
            {requests.map(request => (
              <article key={request.request_id} className={styles.item}>
                <div className={styles.itemHead}>
                  <span className={`${styles.status} ${styles[`status_${request.status}`] ?? ''}`}>{request.status_label}</span>
                  <span className={styles.type}>{request.request_type_label}</span>
                  <time>{formatTime(request.updated_at ?? request.created_at)}</time>
                </div>
                <h3>{request.title}</h3>
                <p className={styles.nextStep}>下一步：{request.next_step}</p>
                {request.needs_materials && <p className={styles.materials}>待补充材料：{request.needs_materials}</p>}
              </article>
            ))}
          </div>
        )}
      </section>
    </main>
  )
}

function formatTime(value: string | null) {
  const date = value ? new Date(value) : null
  return date && !Number.isNaN(date.getTime())
    ? date.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    : '—'
}
