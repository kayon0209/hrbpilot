import { type ReactNode } from 'react'

/**
 * 制度问答回答的轻量 Markdown 渲染。
 *
 * 回答来源有两类：LLM 生成（prompt 约定用 `## 标题` + 数字列表的固定业务结构）
 * 与无依据/低置信度 fallback 模板（同样的结构）。之前 `{answer}` 按纯文本渲染，
 * `##` 直接漏到界面上。
 *
 * 刻意不引入 markdown 库：LLM 输出格式受 prompt 约束（标题/列表/加粗/引用标记），
 * 覆盖这四种就够；少一个依赖就少一个供应链面。所有内容都经 React 元素构造，
 * 不走 dangerouslySetInnerHTML，无 XSS 面。
 *
 * 未知行原样输出——渲染器宁可少渲染，不可改变回答语义。
 */

/** 【引用: 文档名 §章节】 的 inline 引用标记 → 高亮 span */
const CITATION = /【引用:\s*([^】]+)】/g
/** 行内 **加粗** → strong */
const BOLD = /\*\*([^*]+)\*\*/g

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = []
  let cursor = 0
  let match: RegExpExecArray | null
  // 交替扫描引用标记与加粗，按出现位置切分
  const patterns: Array<{ re: RegExp; tag: 'cite' | 'strong' }> = [
    { re: CITATION, tag: 'cite' },
    { re: BOLD, tag: 'strong' },
  ]
  let round = 0
  let guard = 0
  while (cursor < text.length && guard++ < 200) {
    let best: { start: number; end: number; inner: string; tag: 'cite' | 'strong' } | null = null
    for (const { re, tag } of patterns) {
      re.lastIndex = cursor
      match = re.exec(text)
      if (match && (best === null || match.index < best.start)) {
        best = { start: match.index, end: re.lastIndex, inner: match[1], tag }
      }
    }
    if (!best) break
    if (best.start > cursor) nodes.push(text.slice(cursor, best.start))
    nodes.push(
      best.tag === 'cite'
        ? <span className="qa-citation" key={`${keyPrefix}-c${round++}`}>【引用: {best.inner}】</span>
        : <strong key={`${keyPrefix}-b${round++}`}>{best.inner}</strong>,
    )
    cursor = best.end
  }
  if (cursor < text.length) nodes.push(text.slice(cursor))
  return nodes
}

export function MarkdownAnswer({ text }: { text: string }) {
  const lines = text.split('\n')
  const blocks: ReactNode[] = []
  let listItems: ReactNode[] = []

  const flushList = (key: string) => {
    if (listItems.length > 0) {
      blocks.push(<ol key={`ol-${key}`}>{listItems}</ol>)
      listItems = []
    }
  }

  lines.forEach((line, index) => {
    const key = String(index)
    const heading = /^(#{1,4})\s+(.*)$/.exec(line)
    const ordered = /^\s*(\d+)[.、]\s*(.*)$/.exec(line)

    if (heading) {
      flushList(key)
      const level = Math.min(heading[1].length + 2, 5) as 3 | 4 | 5 // ## → h3
      const Tag = `h${level}` as 'h3' | 'h4' | 'h5'
      blocks.push(<Tag key={`h-${key}`}>{renderInline(heading[2], `h${key}`)}</Tag>)
    } else if (ordered) {
      listItems.push(<li key={`li-${key}`}>{renderInline(ordered[2], `li${key}`)}</li>)
    } else if (line.trim() === '') {
      flushList(key)
    } else {
      flushList(key)
      blocks.push(<p key={`p-${key}`}>{renderInline(line, `p${key}`)}</p>)
    }
  })
  flushList('end')

  return <div className="qa-markdown">{blocks}</div>
}
