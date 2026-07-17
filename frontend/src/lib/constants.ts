/** HRBP AI Workbench — constants and route definitions */

export const SCENES = {
  policy_qa: {
    id: 'policy_qa',
    label: '制度问答',
    route: '/policy-qa',
    color: 'indigo',
    icon: 'BookOpen',
    requiredRole: 'employee',
  },
  interview_digest: {
    id: 'interview_digest',
    label: '访谈整理',
    route: '/interview-digest',
    color: 'sky',
    icon: 'FileText',
    requiredRole: 'hrbp',
  },
  voice_insight: {
    id: 'voice_insight',
    label: '声音洞察',
    route: '/voice-insight',
    color: 'emerald',
    icon: 'BarChart3',
    requiredRole: 'hrbp',
  },
  weekly_report: {
    id: 'weekly_report',
    label: '周报生成',
    route: '/weekly-report',
    color: 'amber',
    icon: 'Report',
    requiredRole: 'hrbp',
  },
  culture_content: {
    id: 'culture_content',
    label: '文化传播',
    route: '/culture-content',
    color: 'rose',
    icon: 'PenTool',
    requiredRole: 'hrbp',
  },
} as const;

export const MANAGEMENT_PAGES = {
  kb_management: {
    id: 'kb_management',
    label: '知识库管理',
    route: '/kb-management',
    icon: 'Database',
    requiredRole: 'hr_manager',
  },
  evaluation: {
    id: 'evaluation',
    label: '评测看板',
    route: '/evaluation',
    icon: 'LineChart',
    requiredRole: 'hr_manager',
  },
  settings: {
    id: 'settings',
    label: '系统设置',
    route: '/settings',
    icon: 'Settings',
    requiredRole: 'admin',
  },
} as const;

export type Role = 'employee' | 'hrbp' | 'hr_manager' | 'admin';

export const ROLE_LABELS: Record<Role, string> = {
  employee: '普通员工',
  hrbp: 'HRBP',
  hr_manager: 'HR 经理',
  admin: '管理员',
};

/** Check if a role can access a page with a required minimum role */
export function canAccess(userRole: Role, requiredRole: Role): boolean {
  const hierarchy: Role[] = ['employee', 'hrbp', 'hr_manager', 'admin'];
  return hierarchy.indexOf(userRole) >= hierarchy.indexOf(requiredRole);
}
