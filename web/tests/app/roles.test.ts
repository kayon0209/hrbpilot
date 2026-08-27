import { expect, test } from 'vitest'
import { hasMinimumRole } from '../../src/app/roles'
import { getVisibleNav } from '../../src/app/navigation'

test('higher roles inherit HRBP and HR manager capabilities', () => {
  expect(hasMinimumRole('hrbp', 'hrbp')).toBe(true)
  expect(hasMinimumRole('hr_manager', 'hrbp')).toBe(true)
  expect(hasMinimumRole('admin', 'hr_manager')).toBe(true)
  expect(hasMinimumRole('employee', 'hrbp')).toBe(false)
})

test('navigation matches the backend scene and management visibility matrix', () => {
  const employee = getVisibleNav('employee').map(item => item.to)
  const hrbp = getVisibleNav('hrbp').map(item => item.to)
  const manager = getVisibleNav('hr_manager').map(item => item.to)
  const admin = getVisibleNav('admin').map(item => item.to)

  expect(employee).toEqual(['/', '/policy'])
  expect(hrbp).toEqual(['/', '/policy', '/interview', '/voice', '/weekly', '/culture'])
  expect(manager).toContain('/knowledge')
  expect(manager).toContain('/evaluation')
  expect(manager).not.toContain('/settings')
  expect(admin).toContain('/settings')
})
