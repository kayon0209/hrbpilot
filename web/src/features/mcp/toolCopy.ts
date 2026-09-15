/**
 * 外部工具页的用户语言词典（2026-09-13 普通用户可读性改造）。
 *
 * 后端 `/api/mcp/capabilities` 返回的是开发者视角的数据（工具名、capability、
 * 参数结构 JSON）。这里把它翻译成普通使用者能看懂的东西：
 * 「它做什么」「你可以对 AI 怎么说」「参数各是什么意思」「结果是好是坏」。
 *
 * 新增后端工具时在此补一条即可；缺条目会回落到通用文案，页面不会崩。
 * 注意：字段的**类型/必填**来自后端 schema（见 toolForm.ts），这里只放"怎么说人话"。
 */
import type { McpToolResult, ToolOutcome } from '../../api/mcp'
import type { AgentTaskOutcome } from '../../api/agent-tasks'

export interface ToolCopy {
  /** 中文名，替代技术名做卡片主标题 */
  label: string
  /** 这个工具帮你做什么（一句话，不提任何技术名词） */
  what: string
  /** 一句可以直接对 AI 助手说的话 */
  say: string
  /** 需要提醒的限制；查询类没有 */
  note?: string
}

export const FALLBACK_TOOL_COPY: ToolCopy = {
  label: '扩展工具',
  what: '由管理员配置的额外能力。',
  say: '（暂无示例，请咨询管理员）',
}

export const TOOL_COPY: Record<string, ToolCopy> = {
  search_policy: {
    label: '查询制度',
    what: '在制度资料里按关键词找出相关条款，并告诉你结果出自哪份文件。',
    say: '请假超过三天需要哪些审批？',
  },
  get_policy_source: {
    label: '查看制度原文',
    what: '按文件名调出制度原文，也可以只挑其中某一章看。',
    say: '把《请假管理制度》第 4 章的原文给我。',
  },
  create_hr_case: {
    label: '新建 HR 案件',
    what: '登记一条新的员工事务，例如试用期异常、离职面谈、投诉受理。',
    say: '帮 EMP-001 建一条试用期异常跟进。',
    note: '提交后需 HR 批准才生效',
  },
  assign_case_owner: {
    label: '指派案件负责人',
    what: '把某条案件转给指定的 HR 同学跟进。',
    say: '这条案件转给 hr-manager-9 跟进。',
    note: '提交后需 HR 批准才生效',
  },
  send_case_notification: {
    label: '发送案件通知',
    what: '就某条案件给相关同事发一条站内通知。',
    say: '给部门 HR 发一条政策更新通知。',
    note: '提交后需 HR 批准才生效',
  },
  update_case_status: {
    label: '把案件标记为已解决',
    what: '把某条案件标成「已解决」，表示这件事处理完了。',
    say: '这条案件处理完了，标记为已解决。',
    note: '提交后需 HR 批准才生效',
  },
  create_work_task: {
    label: '新建跟进任务',
    what: '给案件挂一条待办，写清下一步做什么、谁来跟。',
    say: '加一条任务：联系员工补齐劳动合同。',
    note: '提交后需 HR 批准才生效',
  },
}

/**
 * 参数的中文说法，直接当表单字段的标签用。
 *
 * 字段名与控件的对应关系不在这里维护 —— 那来自后端 schema（toolForm.ts），
 * 否则工具一改就会漂移。这里只负责"这个字段用中文怎么说"。
 * 词典里没有的字段回落显示原始字段名（诚实：不编一个可能错的中文名）。
 */
export const FIELD_LABELS: Record<string, string> = {
  case_id: '案件编号',
  query: '你要问的问题',
  kb_id: '制度库编号（不清楚就留空）',
  top_k: '返回几条结果（1–10）',
  document_name: '制度文件名',
  section: '只看某一章（可留空）',
  title: '一句话说明这件事',
  subject_ref: '涉及谁的员工编号',
  category: '类别',
  risk_level: '紧急程度',
  description: '补充说明（可留空）',
  owner_id: '接手人的账号',
  recipient_ref: '发给谁',
  template: '通知模板',
  channel: '发送方式',
  status: '新状态',
  next_action: '下一步要做什么',
  owner_user_id: '由谁负责（可留空）',
  waiting_for: '在等谁或等什么（可留空）',
  due_at: '截止时间（可留空）',
  total_units: '任务总量（可留空）',
}

/**
 * 输入框里的灰字提示。
 *
 * 表单取代了原来那份「参数怎么填」的说明列表，所以填法示例直接写进控件里 ——
 * 用户在这个字段上就能看到该怎么写，不必来回对照两处。
 */
export const FIELD_HINTS: Record<string, string> = {
  case_id: '例如：case-20260913-001',
  query: '例如：请假超过三天需要哪些审批？',
  document_name: '照抄文件名，例如：请假管理制度.pdf',
  section: '例如：第 4 章（不填就返回全文）',
  title: '例如：试用期异常跟进',
  subject_ref: '例如：EMP-001',
  category: '例如：onboarding',
  description: '补充背景，最多 4000 字',
  owner_id: '例如：hr-manager-9',
  recipient_ref: '例如：dept-hr',
  template: '例如：policy_update',
  next_action: '例如：联系员工补充劳动合同',
  owner_user_id: '例如：hr-manager-9',
  waiting_for: '例如：等待员工回传材料',
}

/** 固定取值的中文说法（例如 risk_level、status、channel）。 */
export const OPTION_LABELS: Record<string, string> = {
  LOW: '低',
  MEDIUM: '中',
  HIGH: '高',
  RESOLVED: '已解决',
  in_app: '站内通知',
}

export type ResultTone = 'ok' | 'warn' | 'error'

/** 任务型 outcome 也走同一套语气：草稿/待审批/运行中是"进行中"，不是错误。 */
const TONE_BY_OUTCOME: Record<string, ResultTone> = {
  FOUND: 'ok',
  SUCCEEDED: 'ok',
  // 没查到不是错误：它是"这次没有依据"，用户需要知道但不能被吓到。
  NO_EVIDENCE: 'warn',
  AWAITING_APPROVAL: 'warn',
  AWAITING_CONFIRMATION: 'warn',
  INPUT_REQUIRED: 'warn',
  RUNNING: 'warn',
  CANCELLED: 'warn',
  EXPIRED: 'warn',
  AUTH_REQUIRED: 'warn',
  FORBIDDEN: 'error',
  FAILED: 'error',
}

/**
 * 把工具返回结果概括成一句人话。
 *
 * 优先采用后端契约给出的 `user_message` 与 `outcome` —— 文案与判定都只在一个
 * 地方定义（app/mcp/contract.py），前端不再自己猜"这个返回算成功还是失败"。
 * 下面的兜底分支只服务于没有 outcome 的旧返回。
 */
export function describeResult(res: McpToolResult): { tone: ResultTone; text: string } {
  const message = typeof res.user_message === 'string' && res.user_message ? res.user_message : null

  if (res.outcome && res.outcome in TONE_BY_OUTCOME) {
    return { tone: TONE_BY_OUTCOME[res.outcome], text: message ?? DEFAULT_TEXT[res.outcome] }
  }

  if (res.ok === false) {
    return { tone: 'error', text: message ?? '没有成功，请检查填写内容后重试。' }
  }
  if (typeof res.approval_id === 'string' && res.approval_id) {
    return { tone: 'warn', text: `已生成一条待审批记录（编号 ${res.approval_id}）。请到「团队待处理」批准后才会真正执行。` }
  }
  if (Array.isArray(res.chunks)) {
    return { tone: 'ok', text: `查询完成，找到 ${res.chunks.length} 条相关制度。` }
  }
  if (res.ok === true) {
    return { tone: 'ok', text: message ?? '调用成功。' }
  }
  return { tone: 'warn', text: message ?? '已返回结果，详情见下方原始数据。' }
}

const DEFAULT_TEXT: Record<string, string> = {
  FOUND: '查询完成，已找到相关内容。',
  NO_EVIDENCE: '没有找到匹配的内容。',
  AWAITING_APPROVAL: '已提交，等待 HR 批准后才会真正执行。',
  AWAITING_CONFIRMATION: '草稿已冻结，请核对确认卡并提交审批。',
  INPUT_REQUIRED: '信息还不完整，请补齐后再生成确认卡。',
  RUNNING: '任务正在处理中，可稍后查询状态。',
  SUCCEEDED: '任务已完成。',
  CANCELLED: '任务已取消。',
  EXPIRED: '草稿已过期，请重新发起。',
  AUTH_REQUIRED: '这次调用没有携带身份信息，因此没有返回真实数据。',
  FORBIDDEN: '你当前的权限不包含这项能力。',
  FAILED: '调用没有完成，请稍后重试。',
}

export function describeTaskResult(res: {
  outcome?: AgentTaskOutcome | ToolOutcome | string
  user_message?: string
  ok?: boolean
  approval_id?: string
  chunks?: unknown[]
}): { tone: ResultTone; text: string } {
  if (typeof res.outcome === 'string' && res.outcome in TONE_BY_OUTCOME) {
    return { tone: TONE_BY_OUTCOME[res.outcome], text: res.user_message ?? DEFAULT_TEXT[res.outcome] }
  }
  return describeResult(res as McpToolResult)
}
