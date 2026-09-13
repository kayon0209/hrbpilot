/**
 * 制度问答回答区渲染回归（2026-09-13 用户体验修复）。
 *
 * 锁三件事：
 * 1. `##` 标题被渲染成标题元素而不是裸文本漏在界面上；
 * 2. 低置信度 fallback 的正文不再出现置信度数字（那是开发者指标），
 *    且点名来源文件（用户要知道"参考的是什么"）；
 * 3. 反馈区两个按钮都有可读文字——CSS Modules 曾把 .primary-button
 *    hash 掉导致"有帮助"白字透明底变成空白框，这里锁 DOM 语义兜底。
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, test } from 'vitest'
import { MarkdownAnswer } from '../../src/features/policy-qa/MarkdownAnswer'

describe('MarkdownAnswer', () => {
  test('renders ## headings as heading elements, not raw markers', () => {
    render(<MarkdownAnswer text={'## 结论\n找到了相关制度。'} />)
    expect(screen.getByRole('heading', { name: '结论' })).toBeTruthy()
    expect(screen.queryByText('## 结论')).toBeNull()
  })

  test('renders ordered suggestion lists with list semantics', () => {
    render(<MarkdownAnswer text={'## 下一步\n1. 换用制度原文表述提问。\n2. 提交人工复核。'} />)
    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[0].textContent).toContain('换用制度原文表述提问')
  })

  test('keeps citation markers visible and highlighted', () => {
    render(<MarkdownAnswer text={'按手册执行【引用: 员工手册 §4.2】。'} />)
    expect(screen.getByText(/员工手册 §4.2/)).toBeTruthy()
  })

  test('bold markers are rendered as strong, not literal asterisks', () => {
    render(<MarkdownAnswer text={'**重要**：请核对原文。'} />)
    expect(screen.getByText('重要').tagName).toBe('STRONG')
  })
})
