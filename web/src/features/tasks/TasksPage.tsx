import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  advanceWorkTask,
  createWorkSubtask,
  createWorkTask,
  getAssignableOwners,
  getWorkSummaries,
  updateWorkTask,
  type WorkSummary,
} from '../../api/work-summaries'
import { AsyncState } from '../../components/AsyncState'

function formatDeadline(value: string) {
  // The API stores an ISO-8601 UTC instant (backend returns .isoformat() with
  // tz); render in the user's local timezone instead of showing UTC clock time.
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function toLocalInput(value: string | null) {
  // Convert the stored UTC instant back into the <input type="datetime-local">
  // format (local wall-clock) so editing a task shows the same time the user
  // originally picked.
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function WorkItem({
  item,
  resumable = true,
  owners,
  onChanged,
}: {
  item: WorkSummary
  resumable?: boolean
  owners: { user_id: string; name: string }[]
  onChanged?: () => void
}) {
  const [splitting, setSplitting] = useState(false)
  const [editing, setEditing] = useState(false)
  const [subtaskTitle, setSubtaskTitle] = useState('')
  const [subtaskNextAction, setSubtaskNextAction] = useState('')
  const [subtaskOwner, setSubtaskOwner] = useState('')
  const [subtaskDueAt, setSubtaskDueAt] = useState('')
  const [actionError, setActionError] = useState('')
  const [editTitle, setEditTitle] = useState(item.title)
  const [editNextAction, setEditNextAction] = useState(item.next_action)
  const [editWaitingFor, setEditWaitingFor] = useState(item.waiting_for ?? '')
  const [editDueAt, setEditDueAt] = useState(toLocalInput(item.due_at))
  const [editTotalUnits, setEditTotalUnits] = useState(
    item.total_units !== null ? String(item.total_units) : '',
  )
  const [editOwner, setEditOwner] = useState('')
  // FE-02: re-sync the edit form when the item data refreshes (advance,
  // owner change, other-actor updates). Without this, an open editor keeps
  // stale values and saving can silently overwrite a newer server state.
  useEffect(() => {
    setEditTitle(item.title)
    setEditNextAction(item.next_action)
    setEditWaitingFor(item.waiting_for ?? '')
    setEditDueAt(toLocalInput(item.due_at))
    setEditTotalUnits(item.total_units !== null ? String(item.total_units) : '')
    setEditOwner('')
    setActionError('')
  }, [item.work_id, item.title, item.next_action, item.waiting_for, item.due_at, item.total_units])
  const taskAction = useMutation({
    mutationFn: async (action: 'complete' | 'advance' | 'split' | 'save') => {
      if (action === 'split') {
        if (!subtaskTitle.trim()) throw new Error('请填写子任务名称')
        return createWorkSubtask(item.work_id, {
          title: subtaskTitle.trim(),
          next_action: subtaskNextAction.trim(),
          owner_user_id: subtaskOwner || undefined,
          waiting_for: null,
          due_at: subtaskDueAt ? new Date(subtaskDueAt).toISOString() : null,
          total_units: null,
        })
      }
      if (action === 'advance') {
        // TASK-02: the increment happens server-side in a single guarded
        // UPDATE, so two rapid clicks cannot double-count.
        return advanceWorkTask(item.work_id)
      }
      if (action === 'save') {
        return updateWorkTask(item.work_id, {
          title: editTitle.trim(),
          next_action: editNextAction.trim(),
          waiting_for: editWaitingFor.trim() || null,
          due_at: editDueAt ? new Date(editDueAt).toISOString() : null,
          ...(editTotalUnits ? { total_units: Number(editTotalUnits) } : {}),
          ...(editOwner ? { owner_user_id: editOwner } : {}),
        })
      }
      return updateWorkTask(item.work_id, { status: 'completed' })
    },
    onSuccess: (_data, action) => {
      if (action === 'split') {
        setSubtaskTitle('')
        setSubtaskNextAction('')
        setSubtaskOwner('')
        setSubtaskDueAt('')
        setSplitting(false)
      }
      if (action === 'save') setEditing(false)
      setActionError('')
      onChanged?.()
    },
    onError: (error, action) =>
      setActionError(
        action === 'split'
          ? error instanceof Error ? error.message : '子任务创建失败'
          : action === 'save'
            ? error instanceof Error ? error.message : '任务保存失败'
            : error instanceof Error ? error.message : '任务更新失败',
      ),
  })
  const hasUnitProgress = item.progress_mode === 'units'
    && item.completed_units !== null
    && item.total_units !== null
    && item.total_units > 0

  return (
    <article aria-label={item.title}>
      <strong>{item.title}</strong>
      <p>{item.next_action}</p>
      <div className="task-metadata">
        {item.owner && <span>负责人：{item.owner}</span>}
        {item.waiting_for && <span>等待：{item.waiting_for}</span>}
        {item.due_at && <span>截止：{formatDeadline(item.due_at)}</span>}
        {hasUnitProgress
          ? <span>进度：{item.completed_units}/{item.total_units}</span>
          : <span className={`task-stage task-stage--${item.business_status}`}>阶段：{item.business_status}</span>}
      </div>
      {resumable && <Link to={item.resume_target}>打开</Link>}
      {item.work_type === 'work_task' && item.business_status !== '已完成' && (
        <div className="admin-links">
          <button type="button" onClick={() => taskAction.mutate('complete')} disabled={taskAction.isPending}>
            标记完成
          </button>
          {hasUnitProgress && item.completed_units! < item.total_units! && (
            <button type="button" onClick={() => taskAction.mutate('advance')} disabled={taskAction.isPending}>
              完成一个单位
            </button>
          )}
          <button type="button" onClick={() => setEditing(value => !value)}>编辑任务</button>
          <button type="button" onClick={() => setSplitting(value => !value)}>拆分任务</button>
        </div>
      )}
      {editing && (
        <div className="task-metadata">
          <label>任务名称<input value={editTitle} onChange={event => setEditTitle(event.target.value)} /></label>
          <label>下一步<input value={editNextAction} onChange={event => setEditNextAction(event.target.value)} /></label>
          <label>等待对象<input value={editWaitingFor} onChange={event => setEditWaitingFor(event.target.value)} /></label>
          <label>截止时间<input type="datetime-local" value={editDueAt} onChange={event => setEditDueAt(event.target.value)} /></label>
          <label>真实工作总量<input type="number" min="1" value={editTotalUnits} onChange={event => setEditTotalUnits(event.target.value)} /></label>
          {owners.length > 1 && (
            <label>负责人
              <select value={editOwner} onChange={event => setEditOwner(event.target.value)}>
                <option value="">保持不变</option>
                {owners.map(owner => (
                  <option key={owner.user_id} value={owner.user_id}>{owner.name}</option>
                ))}
              </select>
            </label>
          )}
          <button
            type="button"
            onClick={() => taskAction.mutate('save')}
            disabled={taskAction.isPending || !editTitle.trim()}
          >
            {taskAction.isPending ? '正在保存…' : '保存修改'}
          </button>
        </div>
      )}
      {splitting && (
        <div className="task-metadata">
          <label>子任务名称<input value={subtaskTitle} onChange={event => setSubtaskTitle(event.target.value)} /></label>
          <label>子任务下一步<input value={subtaskNextAction} onChange={event => setSubtaskNextAction(event.target.value)} /></label>
          {owners.length > 1 && (
            <label>子任务负责人
              <select value={subtaskOwner} onChange={event => setSubtaskOwner(event.target.value)}>
                <option value="">跟随父任务</option>
                {owners.map(owner => (
                  <option key={owner.user_id} value={owner.user_id}>{owner.name}</option>
                ))}
              </select>
            </label>
          )}
          <label>子任务截止时间<input type="datetime-local" value={subtaskDueAt} onChange={event => setSubtaskDueAt(event.target.value)} /></label>
          <button
            type="button"
            onClick={() => taskAction.mutate('split')}
            disabled={taskAction.isPending || !subtaskTitle.trim()}
          >
            {taskAction.isPending ? '正在创建…' : '创建子任务'}
          </button>
        </div>
      )}
      {actionError && <p role="alert">{actionError}</p>}
    </article>
  )
}

/**
 * 工作任务 (spec §7.5) — each task shows business stage, next step, owner,
 * waiting-for, deadline. Real-unit progress (x/y) only when a true denominator
 * exists; otherwise stage words. No badges, points or streaks.
 */
export function TasksPage() {
  const queryClient = useQueryClient()
  const work = useQuery({ queryKey: ['work-summaries'], queryFn: getWorkSummaries })
  const ownersQuery = useQuery({ queryKey: ['assignable-owners'], queryFn: getAssignableOwners })
  const owners = ownersQuery.data?.owners ?? []
  const ownersError = ownersQuery.isError ? (ownersQuery.error?.message ?? '无法读取负责人列表') : null
  const [title, setTitle] = useState('')
  const [nextAction, setNextAction] = useState('')
  const [waitingFor, setWaitingFor] = useState('')
  const [dueAt, setDueAt] = useState('')
  const [totalUnits, setTotalUnits] = useState('')
  const [ownerId, setOwnerId] = useState('')
  const [createError, setCreateError] = useState('')
  const refresh = () => { void queryClient.invalidateQueries({ queryKey: ['work-summaries'] }) }
  const createTask = useMutation({
    mutationFn: () => createWorkTask({
      title: title.trim(),
      next_action: nextAction.trim(),
      waiting_for: waitingFor.trim() || null,
      due_at: dueAt ? new Date(dueAt).toISOString() : null,
      total_units: totalUnits ? Number(totalUnits) : null,
      owner_user_id: ownerId || undefined,
    }),
    onSuccess: () => {
      setTitle('')
      setNextAction('')
      setWaitingFor('')
      setDueAt('')
      setTotalUnits('')
      setOwnerId('')
      setCreateError('')
      refresh()
    },
    onError: error => setCreateError(error instanceof Error ? error.message : '任务创建失败'),
  })

  const open: WorkSummary[] = []
  const done: WorkSummary[] = []
  if (work.data) {
    // Deduplicate by work_id (audit P1-3): continue_work and attention share
    // the newest actionable object — rendering both produced duplicate rows
    // and duplicate React keys.
    const seen = new Set<string>()
    for (const item of [...(work.data.continue_work ? [work.data.continue_work] : []), ...work.data.attention]) {
      if (seen.has(item.work_id)) continue
      seen.add(item.work_id)
      open.push(item)
    }
    for (const item of work.data.completed_today) {
      if (seen.has(item.work_id)) continue
      seen.add(item.work_id)
      done.push(item)
    }
  }

  return (
    <main className="page-stack">
      <header className="page-heading">
        <div>
          <span className="eyebrow">工作台</span>
          <h1>工作任务</h1>
          <p>每项任务标明阶段、下一步与等待对象；完成反馈直接指向产出。</p>
        </div>
      </header>

      <section className="panel" aria-labelledby="create-task-heading">
        <h2 id="create-task-heading">新建多日任务</h2>
        <div className="task-metadata">
          <label>任务名称<input value={title} onChange={event => setTitle(event.target.value)} /></label>
          <label>下一步<input value={nextAction} onChange={event => setNextAction(event.target.value)} /></label>
          <label>等待对象<input value={waitingFor} onChange={event => setWaitingFor(event.target.value)} /></label>
          <label>截止时间<input type="datetime-local" value={dueAt} onChange={event => setDueAt(event.target.value)} /></label>
          <label>真实工作总量<input type="number" min="1" value={totalUnits} onChange={event => setTotalUnits(event.target.value)} placeholder="可选" /></label>
          {owners.length > 1 && (
            <label>负责人
              <select value={ownerId} onChange={event => setOwnerId(event.target.value)}>
                <option value="">我自己</option>
                {owners.map(owner => (
                  <option key={owner.user_id} value={owner.user_id}>{owner.name}</option>
                ))}
              </select>
            </label>
          )}
        </div>
        <button
          type="button"
          className="primary-button"
          disabled={!title.trim() || createTask.isPending}
          onClick={() => createTask.mutate()}
        >
          {createTask.isPending ? '正在创建…' : '创建多日任务'}
        </button>
        {createError && <p role="alert">{createError}</p>}
        {ownersError && (
          <p role="alert" className="form-note">
            负责人列表读取失败：{ownersError}。
            <button type="button" className="link-button" onClick={() => ownersQuery.refetch()}>重试</button>
            （未分配负责人时任务归创建者）
          </p>
        )}
      </section>

      {work.isPending && <AsyncState kind="loading" title="正在读取任务" detail="按更新时间聚合各来源。" />}
      {work.isError && (
        <AsyncState
          kind="error"
          title="无法读取任务"
          detail={work.error.message}
          action={<button onClick={() => work.refetch()}>重新读取</button>}
        />
      )}
      {work.data && open.length === 0 && done.length === 0 && (
        <section className="panel">
          <h2>还没有进行中的任务</h2>
          <p>从今日工作的三个首行动作开始：问制度、整理面谈、分析反馈。</p>
          <div className="admin-links">
            <Link to="/policy">问制度</Link>
          </div>
        </section>
      )}
      {work.data && (done.length > 0 || work.data.continue_work) && (
        <section className="panel" aria-labelledby="work-review-heading">
          <h2 id="work-review-heading">工作回顾</h2>
          <p>今天完成 {done.length} 项真实产出。</p>
          {work.data.continue_work && (
            <p>下一步：{work.data.continue_work.title} · {work.data.continue_work.next_action}</p>
          )}
        </section>
      )}
      {open.length > 0 && (
        <section className="panel">
          <h2>进行中</h2>
          <div className="issue-list">
            {open.map(item => (
              <WorkItem key={item.work_id} item={item} owners={owners} onChanged={refresh} />
            ))}
          </div>
        </section>
      )}
      {done.length > 0 && (
        <section className="panel">
          <h2>今天已完成</h2>
          <div className="issue-list">
            {done.map(item => (
              <WorkItem key={item.work_id} item={item} owners={owners} resumable={false} />
            ))}
          </div>
        </section>
      )}
    </main>
  )
}
