/**
 * MCP 工具的用户语言词典（2026-09-13 普通用户可读性改造）。
 *
 * 后端 `/api/mcp/capabilities` 返回的是开发者视角的数据（工具名、capability、
 * input_schema JSON）。这里把每个工具翻译成普通使用者能看懂的三件事：
 * 「它做什么」「你可以对 AI 怎么说」「有什么要留意」。
 *
 * 新增后端工具时在此补一条即可；缺条目会回落到 FALLBACK_TOOL_COPY，
 * 页面不会因为词典缺项而崩。
 */

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
  hrbpilot_ping: {
    label: '检查连接',
    what: '确认 AI 助手和本系统之间的连接是否正常。',
    say: '系统还能连上吗？',
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

/** 在线体验区：每个参数的中文含义，替掉「JSON 里这串英文字段是什么」。 */
export const PARAM_HINTS: Record<string, Record<string, string>> = {
  search_policy: {
    query: '你要问的问题，用一句大白话写',
    kb_id: '制度库编号，管理员提供，不清楚就留空',
    top_k: '返回几条结果（1–10）',
  },
  get_policy_source: {
    document_name: '制度文件名，例如 请假管理制度.pdf',
    section: '只看某一章，可留空',
  },
  hrbpilot_ping: {},
  create_hr_case: {
    case_id: '案件编号，必填',
    title: '一句话说明这件事',
    subject_ref: '涉及谁的员工编号',
    category: '类别，例如 onboarding（入职）',
    risk_level: '紧急程度：LOW / MEDIUM / HIGH',
    description: '补充说明，可留空',
  },
  assign_case_owner: {
    case_id: '案件编号，必填',
    owner_id: '接手人的账号，例如 hr-manager-9',
  },
  send_case_notification: {
    case_id: '案件编号，必填',
    recipient_ref: '发给谁，例如 dept-hr（部门 HR）',
    template: '通知模板，例如 policy_update',
    channel: '发送方式，固定填 in_app（站内）',
  },
  update_case_status: {
    case_id: '案件编号，必填',
    status: '新状态，目前只能填 RESOLVED（已解决）',
  },
  create_work_task: {
    case_id: '案件编号，必填',
    title: '任务标题',
    next_action: '下一步要做什么',
    owner_user_id: '由谁负责，可留空',
    waiting_for: '在等谁或等什么，可留空',
    due_at: '截止时间，可留空',
    total_units: '任务总量，可留空',
  },
}

export type ResultTone = 'ok' | 'warn' | 'error'

/**
 * 把工具返回的原始 JSON 概括成一句人话。
 * 原始数据仍然可看（折叠区），但默认先给结论——这是本次改造的重点之一。
 */
export function describeResult(res: Record<string, unknown>): { tone: ResultTone; text: string } {
  if (res.ok === false) {
    const message = typeof res.message === 'string' ? res.message : '请检查填写内容后重试。'
    return { tone: 'error', text: `没有成功：${message}` }
  }
  if (typeof res.approval_id === 'string' && res.approval_id) {
    return {
      tone: 'warn',
      text: `已生成一条待审批记录（编号 ${res.approval_id}）。请到「团队待处理」批准后才会真正执行。`,
    }
  }
  if (Array.isArray(res.chunks)) {
    return { tone: 'ok', text: `查询完成，找到 ${res.chunks.length} 条相关制度。` }
  }
  if (typeof res.warning === 'string' && res.warning) {
    return { tone: 'warn', text: `已按规则受理，但暂时没取到实时数据（可能是制度库未就绪）。` }
  }
  if (res.ok === true) {
    return { tone: 'ok', text: '调用成功。' }
  }
  return { tone: 'warn', text: '已返回结果，详情见下方原始数据。' }
}
