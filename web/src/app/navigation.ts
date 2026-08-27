import type { UserRole } from '../api/types'
import { hasMinimumRole } from './roles'

export const navigation = [
  { to: '/', label: '概览', group: '工作台' }, { to: '/policy', label: '制度问答', group: '知识' },
  { to: '/knowledge', label: '知识库', group: '知识', minimumRole: 'hr_manager' as const }, { to: '/interview', label: '面谈纪要', group: '人才', minimumRole: 'hrbp' as const },
  { to: '/voice', label: '员工声音', group: '人才', minimumRole: 'hrbp' as const }, { to: '/weekly', label: 'HR 周报', group: '创作', minimumRole: 'hrbp' as const },
  { to: '/culture', label: '文化内容', group: '创作', minimumRole: 'hrbp' as const }, { to: '/evaluation', label: '检索评测', group: '系统', minimumRole: 'hr_manager' as const },
  { to: '/settings', label: '服务设置', group: '系统', minimumRole: 'admin' as const },
]

export function getVisibleNav(role: UserRole | null | undefined) {
  return navigation.filter(item => !item.minimumRole || hasMinimumRole(role, item.minimumRole))
}
