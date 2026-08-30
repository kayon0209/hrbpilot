import { useState } from 'react'
import { expandKeywords, generateCultureContent } from '../../api/content-workflows'
import { AsyncState } from '../../components/AsyncState'
import { ResultDocument } from '../../components/ResultDocument'
import { PermissionNotice } from '../../components/PermissionNotice'
import { useSessionStore } from '../../app/session-store'
import styles from './CultureContentPage.module.css'
import { hasMinimumRole } from '../../app/roles'

export function CultureContentPage() {
  const user = useSessionStore(s => s.user)
  const [raw, setRaw] = useState('')
  const [tone, setTone] = useState('积极向上')
  const [expanded, setExpanded] = useState<string[]>([])
  const [result, setResult] = useState<Record<string, unknown> | null>(null)
  const [pending, setPending] = useState<'expand' | 'generate' | null>(null)
  const [error, setError] = useState('')
  const keywords = raw.split(/[，,、\n]/).map(x => x.trim()).filter(Boolean)

  if (!hasMinimumRole(user?.role, 'hrbp')) {
    return <main className="page-stack"><header className="page-heading"><h1>文化内容</h1></header><PermissionNotice feature="文化内容生成" /></main>
  }

  async function expand() {
    if (!keywords.length) return setError('至少输入一个关键词')
    setPending('expand')
    setError('')
    try {
      setExpanded((await expandKeywords({ keywords, tone })).expanded)
    } catch (e) {
      setError(e instanceof Error ? e.message : '扩展失败')
    } finally {
      setPending(null)
    }
  }

  async function generate() {
    const selected = expanded.length ? expanded : keywords
    if (!selected.length) return setError('至少输入一个关键词')
    setPending('generate')
    setError('')
    try {
      setResult((await generateCultureContent({ keywords: selected, tone, expand_keywords: false })).content)
    } catch (e) {
      setError(e instanceof Error ? e.message : '生成失败')
    } finally {
      setPending(null)
    }
  }

  return <main className="page-stack"><header className="page-heading"><div><span className="eyebrow">文化内容</span><h1>文化内容</h1><p>先生成内容关键词，再选定进入多平台分发。</p></div></header><div className={styles.layout}><section className={styles.controls}><label>核心关键词<textarea rows={5} value={raw} onChange={e => { setRaw(e.target.value); setExpanded([]) }} placeholder="价值观、团队协作、客户第一" /></label><label>内容基调<select value={tone} onChange={e => setTone(e.target.value)}><option>积极向上</option><option>克制真诚</option><option>温暖叙事</option><option>专业正式</option></select></label><div className={styles.actions}><button className="secondary-button" onClick={expand} disabled={!!pending}>{pending === 'expand' ? '正在扩展…' : '扩展关键词'}</button><button className="primary-button" onClick={generate} disabled={!!pending}>{pending === 'generate' ? '正在生成…' : '生成内容'}</button></div>{expanded.length > 0 && <section className={styles.tags}><strong>扩展结果</strong><div>{expanded.map(word => <span key={word}>{word}</span>)}</div></section>}{error && <p className={styles.error}>{error}</p>}<small className={styles.hint}>你可以先查看扩展结果，再决定是否生成内容。</small></section><section className={styles.result}><h2>渠道内容</h2>{!result && pending !== 'generate' && <AsyncState kind="empty" title="等待内容生成" detail="输入关键词后生成四个渠道的内容草稿。" />}{pending === 'generate' && <AsyncState kind="processing" title="正在生成四渠道版本" />}{result && <ResultDocument result={result} />}</section></div></main>
}
