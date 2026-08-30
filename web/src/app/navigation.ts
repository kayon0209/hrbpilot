import type { UserRole } from '../api/types'
import { hasCapability, type Capability } from './roles'

/**
 * Role-based navigation (spec §四) — three product experiences:
 *   employee → 员工服务入口
 *   hrbp / hr_manager → HR 工作台（经理加团队治理入口）
 *   admin → 企业管理后台
 *
 * An entry the current role cannot use is NOT rendered at all — no
 * disabled placeholders.
 */

export interface NavItem {
  to: string
  label: string
  group: string
  capability?: Capability
  roles?: UserRole[]
}

const navigation: NavItem[] = [
  // —— HR 工作台（hrbp 与 hr_manager 共用；employee 不可见）——
  { to: '/', label: '今日工作', group: '工作台' },
  { to: '/policy', label: '制度问答', group: '工作台' },
  { to: '/tasks', label: '工作任务', group: '工作台' },
  { to: '/interview', label: '面谈纪要', group: '工作材料', capability: 'interview_digest' },
  { to: '/voice', label: '员工声音', group: '工作材料', capability: 'voice_insight' },
  { to: '/weekly', label: 'HR 周报', group: '输出与复盘', capability: 'weekly_report' },
  { to: '/culture', label: '文化内容', group: '更多工具', capability: 'culture_content' },
  { to: '/hr-requests', label: '员工请求', group: '员工服务', capability: 'hr_request_triage' },

  // —— 经理团队治理 ——
  { to: '/team', label: '团队待处理', group: '团队治理', capability: 'knowledge_feedback' },
  { to: '/knowledge', label: '知识与反馈', group: '团队治理', capability: 'knowledge_feedback' },
  { to: '/knowledge-base', label: '知识库管理', group: '团队治理', capability: 'kb_management' },

  // —— 管理后台（admin only）——
  { to: '/evaluation', label: 'AI 质量', group: '管理后台', capability: 'evaluation' },
  { to: '/settings', label: '服务设置', group: '管理后台', capability: 'settings' },
]

export function getVisibleNav(role: UserRole | null | undefined): NavItem[] {
  if (role === 'admin') {
    // 管理后台首页：管理员不做 HR 业务，不显示业务入口
    return [
      { to: '/admin', label: '系统状态', group: '管理后台' },
      { to: '/users', label: '用户与权限', group: '管理后台' },
      { to: '/evaluation', label: 'AI 质量', group: '管理后台' },
      { to: '/knowledge-base', label: '知识库管理', group: '管理后台' },
      { to: '/data-sources', label: '数据接入', group: '管理后台' },
      { to: '/settings', label: '服务设置', group: '管理后台' },
      { to: '/audit', label: '审计记录', group: '管理后台' },
    ]
  }
  if (role === 'employee') {
    return [
      { to: '/policy', label: '问 HR', group: '员工服务' },
      { to: '/my-requests', label: '我的请求', group: '员工服务' },
    ]
  }
  // hrbp / hr_manager — HR 工作台；经理额外获得团队治理组
  return navigation.filter(item => !item.capability || hasCapability(role, item.capability))
}

/** Landing route after login, per experience. */
export function getHomePath(role: UserRole | null | undefined): string {
  if (role === 'admin') return '/admin'
  if (role === 'employee') return '/policy'
  return '/'
}
