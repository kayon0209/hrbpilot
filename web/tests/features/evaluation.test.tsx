import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import { MetricCard } from '../../src/features/evaluation/EvaluationPage'

test('labels stub metrics as unsuitable for business decisions', () => {
  render(<MetricCard value={0.7} isStub />)
  expect(screen.getByText('不可用于业务判断')).toBeVisible()
})
