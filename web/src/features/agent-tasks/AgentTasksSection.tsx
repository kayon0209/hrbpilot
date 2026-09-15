import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  cancelAgentTask,
  decideApproval,
  executeApprovedCase,
  getAgentTaskEvents,
  getAgentTaskStatus,
  listAgentTasks,
  listApprovalQueue,
  prepareAgentTask,
  provideAgentTaskInput,
  submitAgentTask,
  type AgentApprovalItem,
  type AgentTaskInfo,
  type MissingField,
} from '../../api/agent-tasks'
import { AsyncState } from '../../components/AsyncState'
import { useSessionStore } from '../../app/session-store'

const STATUS_LABEL: Record<string, string> = {
  input_required: '待补充',
  ready_for_confirmation: '待确认',
  awaiting_approval: '待审批',
  queued: '已排队',
  running: '执行中',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
  expired: '已过期',
  draft: '草稿',
}

function statusLabel(status: string) {
  return STATUS_LABEL[status] ?? status
}

/** 缺字段追问的输入控件：日期给 datetime-local，负责人给姓名文本，其余文本。 */
function MissingFieldInput({
  field,
  value,
  onChange,
}: {
  field: MissingField
  value: string
  onChange: (value: string) => void
}) {
  const type = field.field === 'due_at' ? 'datetime-local' : 'text'
  // 截止时间既支持 ISO 也支持自然语言（如「下周三」）：控件“填日期”覆盖高频，
  // 旁边的灰字提示覆盖口语，避免字段变成“必须填机器格式”的陷阱。
  return (
    <label>
      {field.label}
      <span className="form-hint">{field.field === 'due_at' ? '可填具体日期，也可回字面如“下周三”' : field.question}</span>
      <input type={type} value={value} onChange={event => onChange(event.target.value)} placeholder={field.question} />
    </label>
  )
}

function ConfirmationCard({ task }: { task: AgentTaskInfo }) {
  const missing = task.missing_fields ?? []
  const steps = task.planned_steps ?? []
  return (
    <article className="panel" aria-label="任务确认卡">
      <h3>
        确认卡 · {statusLabel(task.status)}
        <span className="form-note">（风险：{task.risk_level === 'high' ? '高' : task.risk_level === 'medium' ? '中' : '低'}）</span>
      </h3>
      <p>
        <strong>{task.title}</strong>
      </p>
      {steps.length > 0 && (
        <ol>
          {steps.map((step, i) => (
            <li key={i}>
              {step.label} <span className="form-note">（{step.effect}）</span>
            </li>
          ))}
        </ol>
      )}
      <p className="form-note">现在不会直接修改员工数据。确认的是冻结草稿，提交后进入 HR 审批。</p>
      {missing.length > 0 && (
        <div className="task-meta-row">
          {missing.map(field => (
            <span key={field.field}>缺：{field.label}</span>
          ))}
        </div>
      )}
      {task.approval_id && <p className="form-note">审批编号：{task.approval_id}</p>}
    </article>
  )
}

function AgentTaskCard({ item, onOpen }: { item: AgentTaskInfo; onOpen: (taskId: string) => void }) {
  return (
    <article aria-label={item.title}>
      <strong>{item.title}</strong>
      <div className="task-meta-row">
        <span className={`task-stage task-stage--${item.status}`}>状态：{statusLabel(item.status)}</span>
        {item.source_surface && item.source_surface !== 'web' && <span>来源：AI 助手</span>}
        {item.updated_at && <span>更新：{new Date(item.updated_at).toLocaleString()}</span>}
      </div>
      {item.next && <p className="form-note">下一步：{item.next}</p>}
      <div className="admin-links">
        <button type="button" onClick={() => onOpen(item.task_id)}>
          {item.status === 'input_required' || item.status === 'ready_for_confirmation' ? '查看并确认' : '查看状态'}
        </button>
      </div>
    </article>
  )
}

function AgentTaskDetail({ taskId, onClose }: { taskId: string; onClose: () => void }) {
  const queryClient = useQueryClient()
  const detail = useQuery({ queryKey: ['agent-task', taskId], queryFn: () => getAgentTaskStatus(taskId) })
  const events = useQuery({ queryKey: ['agent-task-events', taskId], queryFn: () => getAgentTaskEvents(taskId) })
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({})
  const [actionError, setActionError] = useState('')
  const [submitKey] = useState(() => crypto.randomUUID())

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['agent-task', taskId] })
    void queryClient.invalidateQueries({ queryKey: ['agent-task-events', taskId] })
    void queryClient.invalidateQueries({ queryKey: ['agent-tasks'] })
  }

  const provide = useMutation({
    mutationFn: () => provideAgentTaskInput(taskId, fieldValues),
    onSuccess: () => {
      setFieldValues({})
      setActionError('')
      refresh()
    },
    onError: error => setActionError(error instanceof Error ? error.message : '补充信息失败'),
  })
  const submit = useMutation({
    mutationFn: (draftVersion: number) => submitAgentTask(taskId, draftVersion, submitKey),
    onSuccess: () => {
      setActionError('')
      refresh()
    },
    onError: error => setActionError(error instanceof Error ? error.message : '提交失败'),
  })
  const cancel = useMutation({
    mutationFn: () => cancelAgentTask(taskId),
    onSuccess: () => {
      setActionError('')
      refresh()
    },
    onError: error => setActionError(error instanceof Error ? error.message : '取消失败'),
  })

  if (detail.isPending) {
    return <AsyncState kind="loading" title="正在读取任务" detail="读取冻结草稿与真实状态。" />
  }
  if (detail.isError) {
    return (
      <AsyncState
        kind="error"
        title="无法读取任务"
        detail={detail.error.message}
        action={<button onClick={() => detail.refetch()}>重新读取</button>}
      />
    )
  }

  const info = detail.data as AgentTaskInfo & { error_code?: string; missing_fields?: MissingField[]; draft_version?: number }
  const missing = info.missing_fields ?? []
  const canSubmit = info.status === 'ready_for_confirmation' && typeof info.draft_version === 'number'
  const canEdit = info.status === 'input_required' || info.status === 'ready_for_confirmation'
  const canCancel = !['succeeded', 'failed', 'cancelled', 'expired'].includes(info.status)

  return (
    <section className="panel" aria-label="任务详情">
      <ConfirmationCard
        task={{
          task_id: taskId,
          task_type: info.task_type ?? '',
          title: (info.title as string) ?? '',
          status: info.status ?? '',
          risk_level: (info.risk_level as string) ?? 'medium',
          source_surface: (info.source_surface as string) ?? 'web',
          updated_at: (info.updated_at as string) ?? null,
          next: (info.next as string) ?? '',
          missing_fields: missing,
          planned_steps: (info.planned_steps as AgentTaskInfo['planned_steps']) ?? [],
          approval_id: (info.approval_id as string | null) ?? null,
        }}
      />
      {canEdit && missing.length > 0 && (
        <div className="task-metadata">
          {missing.map(field => (
            <MissingFieldInput
              key={field.field}
              field={field}
              value={fieldValues[field.field] ?? ''}
              onChange={value => setFieldValues(prev => ({ ...prev, [field.field]: value }))}
            />
          ))}
          <button type="button" className="primary-button" disabled={provide.isPending} onClick={() => provide.mutate()}>
            {provide.isPending ? '正在保存…' : '保存补充信息'}
          </button>
        </div>
      )}
      {canSubmit && (
        <div className="admin-links">
          <button
            type="button"
            className="primary-button"
            disabled={submit.isPending}
            onClick={() => submit.mutate(info.draft_version as number)}
          >
            {submit.isPending ? '正在提交…' : `确认无误，提交审批（v${info.draft_version}）`}
          </button>
          <span className="form-note">提交的是冻结草稿 v{info.draft_version}，不会再改。</span>
        </div>
      )}
      {canCancel && (
        <div className="admin-links">
          <button type="button" disabled={cancel.isPending} onClick={() => cancel.mutate()}>
            {cancel.isPending ? '正在取消…' : '取消这个任务'}
          </button>
        </div>
      )}
      {actionError && <p role="alert">{actionError}</p>}
      {(events.data?.events?.length ?? 0) > 0 && (
        <details>
          <summary>任务时间线（{events.data?.events.length} 条）</summary>
          <ol>
            {events.data?.events.map(event => (
              <li key={event.seq}>
                {event.at && <span>{new Date(event.at).toLocaleString()} </span>}
                {event.from_status && <span>{statusLabel(event.from_status)} → </span>}
                {event.to_status && <span>{statusLabel(event.to_status)} </span>}
                <span className="form-note">{event.event_type}</span>
              </li>
            ))}
          </ol>
        </details>
      )}
      <div className="admin-links">
        <button type="button" onClick={onClose}>
          返回列表
        </button>
      </div>
    </section>
  )
}

/** 审批队列的一行。批准与驳回都调既有 hr-cases 接口；驳回要求写理由 ——
 *  驳回没理由，提交人只能靠猜，来回沟通的成本比一次输入高得多。
 *
 *  「批准并执行」实际是**两次**后端调用（approve → execute）：后端刻意拆成
 *  先决定、再执行两步，各自留审计。没有后台 worker 消费已批准的审批 ——
 *  所以这一步必须由这里触发，否则批准完的任务会永远停在"已排队"。
 *  执行失败时审批决定**仍然有效**，错误要如实说清是执行那一步失败了。 */
function ApprovalRow({ item, onDecided }: { item: AgentApprovalItem; onDecided: () => void }) {
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState('')
  const [error, setError] = useState('')
  const decide = useMutation({
    mutationFn: async (decision: 'approve' | 'reject') => {
      await decideApproval(item.case_id, item.approval_id, decision, decision === 'reject' ? reason : undefined)
      if (decision === 'approve') {
        // 两步之间不共享事务：第二步失败不影响第一步已成立的审批决定。
        await executeApprovedCase(item.case_id, item.approval_id, crypto.randomUUID())
      }
    },
    onSuccess: () => {
      setError('')
      onDecided()
    },
    onError: e => {
      const message = e instanceof Error ? e.message : '操作失败'
      setError(error => error || message)
    },
  })
  const busy = decide.isPending

  return (
    <article aria-label={`待审批：${item.title}`}>
      <strong>{item.title}</strong>
      <div className="task-meta-row">
        <span className="task-stage task-stage--awaiting_approval">状态：待审批</span>
        <span>提交人：{item.requester_name}</span>
        <span>风险：{item.risk_level === 'high' ? '高' : item.risk_level === 'medium' ? '中' : '低'}</span>
        {item.updated_at && <span>提交：{new Date(item.updated_at).toLocaleString()}</span>}
      </div>
      <p className="form-note">审批编号：{item.approval_id}</p>
      {error && (
        <p role="alert">
          {error}
          {/* 批准已生效而执行失败时，用户需要知道决定没有丢。 */}
          {decide.isError && <button type="button" onClick={() => decide.reset()}>知道了</button>}
        </p>
      )}
      {!rejecting ? (
        <div className="admin-links">
          <button type="button" className="primary-button" disabled={busy} onClick={() => decide.mutate('approve')}>
            {busy ? '正在处理…' : '批准并执行'}
          </button>
          <button type="button" disabled={busy} onClick={() => setRejecting(true)}>
            驳回…
          </button>
        </div>
      ) : (
        <div className="task-metadata">
          <label>
            驳回理由
            <span className="form-hint">提交人会看到这段话，说清缺什么或为什么不行</span>
            <input
              value={reason}
              onChange={event => setReason(event.target.value)}
              placeholder="例如：案件还缺员工确认记录，补齐后再提交"
            />
          </label>
          <button
            type="button"
            disabled={busy || !reason.trim()}
            onClick={() => decide.mutate('reject')}
          >
            {busy ? '正在处理…' : '确认驳回'}
          </button>
          <button type="button" disabled={busy} onClick={() => setRejecting(false)}>
            取消
          </button>
        </div>
      )}
    </article>
  )
}

function ApprovalQueuePanel() {
  const queryClient = useQueryClient()
  const queue = useQuery({ queryKey: ['agent-approvals'], queryFn: listApprovalQueue })
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['agent-approvals'] })
    void queryClient.invalidateQueries({ queryKey: ['agent-tasks'] })
  }

  if (queue.isPending) return <AsyncState kind="loading" title="正在读取待审批列表" />
  if (queue.isError) {
    return (
      <AsyncState
        kind="error"
        title="无法读取待审批列表"
        detail={queue.error.message}
        action={<button onClick={() => queue.refetch()}>重新读取</button>}
      />
    )
  }
  const items = queue.data?.items ?? []
  return (
    <section className="panel" aria-label="待审批">
      <h2>待审批（{items.length}）</h2>
      <p className="form-note">
        这些是 AI 助手替人发起、等 HR 经理决定的办理。批准后系统才会执行；驳回要写理由，提交人能看到。
      </p>
      {items.length === 0 ? (
        <p className="form-note">当前没有等待审批的办理。</p>
      ) : (
        <div className="issue-list">
          {items.map(item => (
            <ApprovalRow key={item.task_id} item={item} onDecided={refresh} />
          ))}
        </div>
      )}
    </section>
  )
}

export function AgentTasksSection() {
  const queryClient = useQueryClient()
  // 审批队列只对决定人（hr_manager）可见 —— 后端 ``/approvals`` 有同样的角色门，
  // 前端这里只是不把一个必然 403 的入口摆出来。
  const role = useSessionStore(state => state.user?.role)
  const isDecider = role === 'hr_manager'
  // 默认「全部」而不是「待我处理」：进到这个分区的人多数是**来看进度**的
  // （"我提交的那件事到哪了"），而"待我处理"只含需要补信息/确认的两种状态，
  // 恰恰不含 MCP 提交之后的 `awaiting_approval` —— 拿它当默认，提交者进来什么都看不到。
  const [tab, setTab] = useState<'approvals' | 'needs_me' | 'all'>('all')
  const activeTab = tab === 'approvals' && !isDecider ? 'all' : tab
  const [goal, setGoal] = useState('')
  const [openTaskId, setOpenTaskId] = useState<string | null>(null)
  const [createError, setCreateError] = useState('')

  const list = useQuery({
    queryKey: ['agent-tasks', tab],
    queryFn: () =>
      // 后端按发起人过滤；前端 tab 只做分组展示，不重新定义可见性。
      listAgentTasks(),
  })
  const items = useMemo(() => list.data?.items ?? [], [list.data])
  const needsMe = useMemo(
    () => items.filter(item => item.status === 'input_required' || item.status === 'ready_for_confirmation'),
    [items],
  )
  const shown = activeTab === 'needs_me' ? needsMe : items

  const prepare = useMutation({
    mutationFn: () => prepareAgentTask(goal.trim()),
    onSuccess: data => {
      setGoal('')
      setCreateError('')
      void queryClient.invalidateQueries({ queryKey: ['agent-tasks'] })
      if (data.task_id) setOpenTaskId(data.task_id)
    },
    onError: error => setCreateError(error instanceof Error ? error.message : '任务发起失败'),
  })

  return (
    <section aria-label="AI 发起任务">
      <div className="panel" aria-labelledby="agent-create-heading">
        <h2 id="agent-create-heading">用一句话发起任务</h2>
        <p className="form-note">
          例如：「帮张三建一个试用期跟进任务，下周三前由李经理处理」。系统先生成冻结草稿，你核对确认卡后再提交审批。
        </p>
        <div className="task-metadata">
          <label>
            你想办的事
            <input value={goal} onChange={event => setGoal(event.target.value)} placeholder="例如：把这个案件转给李经理负责" />
          </label>
        </div>
        <button
          type="button"
          className="primary-button"
          disabled={!goal.trim() || prepare.isPending}
          onClick={() => prepare.mutate()}
        >
          {prepare.isPending ? '正在生成草稿…' : '生成任务草稿'}
        </button>
        {createError && <p role="alert">{createError}</p>}
        {prepare.data?.error_code === 'TASK_TYPE_UNDETECTED' && (
          <p role="alert" className="form-note">
            这句话还没有对应的任务类型：换一种说法，或到<Link to="/policy">制度问答</Link>先查依据。
          </p>
        )}
      </div>

      <div className="admin-links" role="tablist" aria-label="AI 任务分组">
        {isDecider && (
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'approvals'}
            onClick={() => setTab('approvals')}
          >
            待审批
          </button>
        )}
        <button type="button" role="tab" aria-selected={activeTab === 'needs_me'} onClick={() => setTab('needs_me')}>
          待我处理（{needsMe.length}）
        </button>
        <button type="button" role="tab" aria-selected={activeTab === 'all'} onClick={() => setTab('all')}>
          全部（{items.length}）
        </button>
      </div>

      {openTaskId && <AgentTaskDetail taskId={openTaskId} onClose={() => setOpenTaskId(null)} />}

      {activeTab === 'approvals' ? (
        <ApprovalQueuePanel />
      ) : (
        <>
          {list.isPending && <AsyncState kind="loading" title="正在读取 AI 发起的任务" detail="只列出你自己发起的任务。" />}
          {list.isError && (
            <AsyncState
              kind="error"
              title="无法读取任务"
              detail={list.error.message}
              action={<button onClick={() => list.refetch()}>重新读取</button>}
            />
          )}
          {list.data && shown.length === 0 && (
            <section className="panel">
              <h2>这里还没有任务</h2>
              <p>在上面用一句话发起，或让你的 AI 助手（Codex / WorkBuddy）替你发起。</p>
            </section>
          )}
          {shown.length > 0 && (
            <section className="panel">
              <h2>{activeTab === 'needs_me' ? '待我处理' : '全部任务'}</h2>
              <div className="issue-list">
                {shown.map(item => (
                  <AgentTaskCard key={item.task_id} item={item} onOpen={setOpenTaskId} />
                ))}
              </div>
            </section>
          )}
        </>
      )}
    </section>
  )
}
