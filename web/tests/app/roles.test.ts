import { expect, test } from 'vitest'
import { hasCapability, hasMinimumRole, canAccessPath } from '../../src/app/roles'
import { getVisibleNav, getHomePath } from '../../src/app/navigation'

test('capability matrix has no linear inheritance — admin holds no business content', () => {
  // admin is a platform role, not a business role (spec §3.2)
  expect(hasCapability('admin', 'interview_digest')).toBe(false)
  expect(hasCapability('admin', 'voice_insight')).toBe(false)
  expect(hasCapability('admin', 'weekly_report')).toBe(false)
  expect(hasCapability('admin', 'culture_content')).toBe(false)
  expect(hasCapability('admin', 'hr_case')).toBe(false)
  expect(hasCapability('admin', 'policy_qa')).toBe(false)
  // admin keeps platform capabilities
  expect(hasCapability('admin', 'evaluation')).toBe(true)
  expect(hasCapability('admin', 'settings')).toBe(true)
})

test('employee only reaches policy QA among business capabilities', () => {
  expect(hasCapability('employee', 'policy_qa')).toBe(true)
  expect(hasCapability('employee', 'interview_digest')).toBe(false)
  expect(hasCapability('employee', 'kb_management')).toBe(false)
})

test('hrbp holds the five business scenes; hr_manager adds governance', () => {
  for (const cap of ['policy_qa', 'interview_digest', 'voice_insight', 'weekly_report', 'culture_content', 'hr_case'] as const) {
    expect(hasCapability('hrbp', cap)).toBe(true)
    expect(hasCapability('hr_manager', cap)).toBe(true)
  }
  expect(hasCapability('hr_manager', 'knowledge_feedback')).toBe(true)
  expect(hasCapability('hr_manager', 'kb_management')).toBe(false)
  expect(hasCapability('hrbp', 'kb_management')).toBe(false)
})

test('legacy hasMinimumRole helpers route through capabilities', () => {
  expect(hasMinimumRole('hrbp', 'hrbp')).toBe(true)
  expect(hasMinimumRole('employee', 'hrbp')).toBe(false)
  // admin is NOT "above" hrbp anymore
  expect(hasMinimumRole('admin', 'hrbp')).toBe(false)
  expect(hasMinimumRole('admin', 'admin')).toBe(true)
})

test('canAccessPath blocks admin from business routes', () => {
  expect(canAccessPath('admin', '/interview')).toBe(false)
  expect(canAccessPath('admin', '/evaluation')).toBe(true)
  expect(canAccessPath('employee', '/policy')).toBe(true)
  expect(canAccessPath('employee', '/settings')).toBe(false)
})

test('my-requests belongs to employees only — cross-role logins never inherit it (audit P1-2)', () => {
  // Regression: a stale `from=/my-requests` used to strand hrbp on /forbidden
  // because the path was gated by policy_qa, which every HR role holds.
  expect(canAccessPath('employee', '/my-requests')).toBe(true)
  expect(canAccessPath('hrbp', '/my-requests')).toBe(false)
  expect(canAccessPath('hr_manager', '/my-requests')).toBe(false)
  expect(canAccessPath('admin', '/my-requests')).toBe(false)
  // The path capability mirrors the backend employee_request capability, not policy_qa.
  expect(hasCapability('employee', 'employee_request')).toBe(true)
  expect(hasCapability('hrbp', 'employee_request')).toBe(false)
})

test('frontend capability matrix mirrors the backend rbac matrix (audit P2-6)', () => {
  // Backend registers work_summary for hrbp/hr_manager only, data_source_admin
  // for admin only, employee_request for employee only (app/access/middleware/rbac.py).
  expect(hasCapability('employee', 'work_summary')).toBe(false)
  expect(hasCapability('hrbp', 'work_summary')).toBe(true)
  expect(hasCapability('hr_manager', 'work_summary')).toBe(true)
  expect(hasCapability('admin', 'work_summary')).toBe(false)
  expect(hasCapability('admin', 'data_source_admin')).toBe(true)
  expect(hasCapability('hrbp', 'data_source_admin')).toBe(false)
  // Tasks page is powered by the work-summary API — gate on the same capability
  // so a role that can see the page can always load its data.
  expect(canAccessPath('hrbp', '/tasks')).toBe(true)
  expect(canAccessPath('admin', '/tasks')).toBe(false)
  expect(canAccessPath('employee', '/tasks')).toBe(false)
})

test('navigation renders three distinct experiences', () => {
  const employee = getVisibleNav('employee').map(item => item.to)
  const hrbp = getVisibleNav('hrbp').map(item => item.to)
  const manager = getVisibleNav('hr_manager').map(item => item.to)
  const admin = getVisibleNav('admin').map(item => item.to)

  // employee: 问 HR + 我的请求 only
  expect(employee).toEqual(['/policy', '/my-requests'])
  // hrbp: today workspace + business scenes, no governance
  expect(hrbp).toContain('/interview')
  expect(hrbp).toContain('/voice')
  expect(hrbp).not.toContain('/knowledge')
  expect(hrbp).not.toContain('/evaluation')
  expect(hrbp).not.toContain('/settings')
  expect(hrbp).not.toContain('/team')
  // manager: adds team governance, still no admin pages
  expect(manager).toContain('/team')
  expect(manager).toContain('/knowledge')
  expect(manager).not.toContain('/knowledge-base')
  expect(manager).not.toContain('/evaluation')
  expect(manager).not.toContain('/settings')
  // admin: platform pages only, no business entries
  expect(admin).toEqual(['/admin', '/users', '/evaluation', '/knowledge-base', '/data-sources', '/settings', '/audit'])
})

test('each experience lands on its own home after login', () => {
  expect(getHomePath('employee')).toBe('/policy')
  expect(getHomePath('hrbp')).toBe('/')
  expect(getHomePath('hr_manager')).toBe('/')
  expect(getHomePath('admin')).toBe('/admin')
})
