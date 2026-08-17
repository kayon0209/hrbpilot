import { expect, test } from 'vitest'
import { hasMinimumRole } from '../../src/app/roles'

test('higher roles inherit HRBP and HR manager capabilities', () => {
  expect(hasMinimumRole('hrbp', 'hrbp')).toBe(true)
  expect(hasMinimumRole('hr_manager', 'hrbp')).toBe(true)
  expect(hasMinimumRole('admin', 'hr_manager')).toBe(true)
  expect(hasMinimumRole('employee', 'hrbp')).toBe(false)
})
