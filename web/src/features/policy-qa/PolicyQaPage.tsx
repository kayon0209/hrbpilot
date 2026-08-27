import { type FormEvent, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { listPolicyKnowledgeBases, streamPolicyAnswer, submitPolicyFeedback, type PolicySource } from '../../api/policy-qa'
import { AsyncState } from '../../components/AsyncState'
import styles from './PolicyQaPage.module.css'

export function PolicyQaPage() {
  const kbs = useQuery({ queryKey: ['policy-kbs'], queryFn: listPolicyKnowledgeBases })
  const [kbId, setKbId] = useState('')
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState('')
  const [sources, setSources] = useState<PolicySource[]>([])
  const [phase, setPhase] = useState<'idle' | 'streaming' | 'done' | 'error'>('idle')
  const [error, setError] = useState('')
  const [meta, setMeta] = useState<{ message_id?: string; confidence?: number; latency_ms?: number; has_evidence?: boolean }>({})
  const [feedbackSent, setFeedbackSent] = useState(false)
  const controller = useRef<AbortController | null>(null)

  const selected = kbId || kbs.data?.[0]?.id || ''

  async function ask(event: FormEvent) {
    event.preventDefault()
    if (!question.trim() || !selected) return

    controller.current?.abort()
    const next = new AbortController()
    controller.current = next
    setAnswer('')
    setSources([])
    setMeta({})
    setError('')
    setPhase('streaming')
    setFeedbackSent(false)

    try {
      await streamPolicyAnswer(
        { question: question.trim(), kb_id: selected },
        next.signal,
        event => {
          if (event.type === 'delta') setAnswer(value => value + event.data.text)
          if (event.type === 'citation') setSources(event.data)
          if (event.type === 'correction') setAnswer(event.data.full_text)
          if (event.type === 'complete') {
            setMeta(event.data)
            setPhase('done')
          }
          if (event.type === 'error') {
            setError(event.data.message)
            setPhase('error')
          }
        },
      )
      if (!next.signal.aborted) setPhase(value => (value === 'streaming' ? 'done' : value))
    } catch (e) {
      if (!next.signal.aborted) {
        setError(e instanceof Error ? e.message : '问答失败')
        setPhase('error')
      }
    }
  }

  function sendFeedback(rating: 'up' | 'down') {
    if (!meta.message_id) {
      setError('当前回答缺少消息标识，无法提交反馈。')
      return
    }
    void submitPolicyFeedback(meta.message_id, rating)
    setFeedbackSent(true)
  }

  return <main className="page-stack"><header className="page-heading"><div><span className="eyebrow">HYBRID RETRIEVAL / VERIFIED SOURCES</span><h1>制度问答</h1><p>答案必须落回制度原文；没有证据时，系统会明确说明。</p></div></header>
    <div className={styles.grid}><section className={styles.question}><form onSubmit={ask}><label>知识库<select value={selected} onChange={e => setKbId(e.target.value)} disabled={kbs.isPending}>{kbs.data?.map(kb => <option key={kb.id} value={kb.id}>{kb.name}</option>)}</select></label><label>问题<textarea rows={7} value={question} onChange={e => setQuestion(e.target.value)} placeholder="例如：请假超过三天需要经过哪些审批？" maxLength={2000} /></label><div className={styles.actions}><small>{question.length} / 2000</small>{phase === 'streaming' ? <button type="button" className="secondary-button" onClick={() => { controller.current?.abort(); setPhase('idle') }}>停止生成</button> : <button className="primary-button" disabled={!selected || !question.trim()}>发送问题</button>}</div></form><p className="truth-note">历史记录接口当前不持久化，本页只保留当前会话结果。</p></section>
      <section className={styles.answer} aria-live="polite"><div className={styles.answerHead}><h2>回答</h2>{meta.latency_ms && <small>{meta.latency_ms} ms</small>}</div>{phase === 'idle' && <AsyncState kind="empty" title="等待问题" detail="选择已索引的制度知识库后开始查询。" />}{phase === 'streaming' && !answer && (
  <div className={styles.retrieving} role="status" aria-busy="true">
    <span className={styles.retrievingSymbol} aria-hidden="true"><span className={styles.retrievingDots}><i className={styles.dotSemantic} /><i className={styles.dotKeyword} /></span></span>
    <div><strong>正在检索并组织答案</strong><p>同时执行语义召回与关键词召回。</p></div>
  </div>
)}{phase === 'error' && <AsyncState kind="error" title="问答未完成" detail={error} />}{answer && <article className={styles.prose}>{answer}</article>}{phase === 'done' && <div className={styles.feedback}>{feedbackSent ? <span className={styles.feedbackThanks}>谢谢，已记录你的判断。</span> : <><span>{meta.has_evidence === false ? '证据不足，请谨慎采用' : '这个回答有帮助吗？'}</span><button onClick={() => sendFeedback('up')}>有帮助</button><button onClick={() => sendFeedback('down')}>需改进</button></>}</div>}</section>
    </div><EvidencePanel sources={sources} />
  </main>
}

function EvidencePanel({ sources }: { sources: PolicySource[] }) {
  return <section className={styles.evidence}><div className={styles.evidenceHead}><div><span className="eyebrow">RETRIEVAL TRACE</span><h2>依据与来源</h2></div><div className={styles.legend}><span>语义相关</span><span>条款关键词</span><span>综合排序</span></div></div>{sources.length === 0 ? <p className={styles.empty}>本次结果尚无可展示的引用片段。</p> : <div className={styles.sources}>{sources.map((source, index) => <article key={`${source.document_name}-${index}`}><span>{String(index + 1).padStart(2, '0')}</span><div><h3>{source.document_name}</h3>{source.section && source.section !== 'unknown' && <small>{source.section}</small>}<p>{source.content_snippet}</p></div>{source.confidence != null && <b>{Math.round(source.confidence * 100)}%</b>}</article>)}</div>}</section>
}
