import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AsyncState } from '../../components/AsyncState'
import { listAssignees, listHrRequests, triageHrRequest, type HrRequestItem } from '../../api/hr-requests'
import { useSessionStore } from '../../app/session-store'
import styles from './HrRequestsPage.module.css'

/**
 * HR 请求分诊 (spec §7.9) — the HR side of the employee service contract.
 *
 * The triage form ALWAYS requires an employee-facing next step; internal notes
 * (hr_note) are stored for HR only and never shown to the employee. Status
 * transitions: 待补充 / 处理中 / 已解决. Managers can additionally assign an
 * owner (audit P1-7) so the request reaches an HRBP's queue.
 */
export function HrRequestsPage() {
  const queryClient = useQueryClient()
  const role = useSessionStore(s => s.user?.role)
  const queue = useQuery({ queryKey: ['hr-requests'], queryFn: listHrRequests })
  const assignees = useQuery({ queryKey: ['hr-request-assignees'], queryFn: listAssignees, enabled: role === 'hr_manager' })
  const triage = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Parameters<typeof triageHrRequest>[1] }) => triageHrRequest(id, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['hr-requests'] }),
  })

  const requests = queue.data?.requests ?? []

  return (
    <main className="page-stack">
      <header className="page-heading">
        <div>
          <span className="eyebrow">员工服务</span>
          <h1>员工请求</h1>
          <p>员工从「问 HR」无法解决时提交的请求。处理时必须给员工一个明确的下一步；内部备注不会展示给员工。</p>
        </div>
      </header>

      {queue.isPending && <AsyncState kind="loading" title="正在读取请求" />}
      {queue.isError && (
        <AsyncState kind="error" title="请求读取失败" detail={queue.error.message} action={<button onClick={() => queue.refetch()}>重试</button>} />
      )}
      {queue.data && requests.length === 0 && (
        <section className="panel">
          <h2>{role === 'hrbp' ? '当前没有指派给你的员工请求' : '当前没有待处理的员工请求'}</h2>
          <p>{role === 'hrbp' ? '经理指派给你的请求会出现在这里。' : '员工提交的新请求会出现在这里，按提交时间排序。'}</p>
        </section>
      )}
      {requests.length > 0 && (
        <section className="panel" aria-labelledby="queue-heading">
          <h2 id="queue-heading">待处理队列</h2>
          <div className={styles.list}>
            {requests.map(request => (
              <RequestCard
                key={request.request_id}
                request={request}
                isManager={role === 'hr_manager'}
                assignees={assignees.data?.assignees ?? []}
                onTriage={triage.mutateAsync}
                pending={triage.isPending && triage.variables?.id === request.request_id}
                error={triage.isError && triage.variables?.id === request.request_id ? triage.error.message : null}
              />
            ))}
          </div>
        </section>
      )}
    </main>
  )
}

function RequestCard({ request, isManager, assignees, onTriage, pending, error }: {
  request: HrRequestItem
  isManager: boolean
  assignees: { user_id: string; name: string; email: string }[]
  onTriage: (input: { id: string; body: Parameters<typeof triageHrRequest>[1] }) => Promise<unknown>
  pending: boolean
  error: string | null
}) {
  const [open, setOpen] = useState(false)
  const [status, setStatus] = useState<'needs_materials' | 'in_progress' | 'resolved'>('in_progress')
  const [nextStep, setNextStep] = useState('')
  const [materials, setMaterials] = useState('')
  const [note, setNote] = useState('')
  const [assigneeId, setAssigneeId] = useState(request.hr_owner_id ?? '')

  async function submit() {
    try {
      await onTriage({
        id: request.request_id,
        body: {
          status,
          next_step_for_employee: nextStep.trim() || undefined,
          needs_materials: materials.trim() || undefined,
          hr_note: note.trim() || undefined,
          hr_owner_id: isManager && assigneeId.trim() ? assigneeId.trim() : undefined,
        },
      })
      // Close only after the server accepted the result — closing on click
      // used to discard the user's typed input on any failure (audit P2-3).
      setOpen(false)
    } catch {
      /* the card-level error state keeps the form open with inputs intact */
    }
  }

  const selectedAssignee = assignees.find(a => a.user_id === (request.hr_owner_id ?? ''))

  return (
    <article className={styles.card}>
      <div className={styles.head}>
        <span className={`${styles.status} ${styles[`status_${request.status}`] ?? ''}`}>{request.status_label}</span>
        <span className={styles.type}>{request.request_type_label}</span>
        <time>{request.created_at?.slice(5, 16).replace('T', ' ')}</time>
      </div>
      <h3>{request.title}</h3>
      <p className={styles.description}>{request.description}</p>
      {request.needs_materials && <p className={styles.materials}>待补充材料：{request.needs_materials}</p>}
      {request.hr_note && <p className={styles.internalNote}>内部备注（员工不可见）：{request.hr_note}</p>}
      <p className={styles.currentNext}>当前下一步（员工视角）：{request.next_step}</p>
      {request.hr_owner_id && <p className={styles.currentNext}>负责人：{selectedAssignee ? `${selectedAssignee.name}（${selectedAssignee.email}）` : '已指派'}</p>}
      {open ? (
        <div className={styles.form}>
          <label>处理状态
            <select value={status} onChange={e => setStatus(e.target.value as typeof status)}>
              <option value="needs_materials">待补充（需说明缺什么）</option>
              <option value="in_progress">处理中</option>
              <option value="resolved">已解决</option>
            </select>
          </label>
          {status === 'needs_materials' ? (
            <label>需要员工补充的材料<textarea rows={2} value={materials} onChange={e => setMaterials(e.target.value)} placeholder="例如：请补充用途说明和身份证明" /></label>
          ) : (
            <label>给员工的下一步（必填）<textarea rows={2} value={nextStep} onChange={e => setNextStep(e.target.value)} placeholder="例如：正在开具，预计明天可在前台领取" /></label>
          )}
          {isManager && (
            <label>指派负责人（可选）
              <select value={assigneeId} onChange={e => setAssigneeId(e.target.value)}>
                <option value="">不指派，由我处理</option>
                {assignees.map(a => <option key={a.user_id} value={a.user_id}>{a.name}（{a.email}）</option>)}
              </select>
            </label>
          )}
          <label>内部备注（员工不可见）<textarea rows={2} value={note} onChange={e => setNote(e.target.value)} placeholder="仅 HR 团队可见的处理说明" /></label>
          {error && <p role="alert">保存未成功：{error}</p>}
          <div className={styles.actions}>
            <button className="primary-button" disabled={pending || (status === 'needs_materials' ? !materials.trim() : !nextStep.trim())} onClick={submit}>
              {pending ? '正在保存…' : '保存处理结果'}
            </button>
            <button className="secondary-button" onClick={() => setOpen(false)}>取消</button>
          </div>
        </div>
      ) : (
        <button className="secondary-button" onClick={() => setOpen(true)}>处理</button>
      )}
    </article>
  )
}
