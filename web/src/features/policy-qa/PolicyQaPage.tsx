import { type FormEvent, useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { listPolicyKnowledgeBases, listPolicySessions, getPolicySessionMessages, streamPolicyAnswer, submitPolicyFeedback, type PolicySource } from '../../api/policy-qa'
import { AsyncState } from '../../components/AsyncState'
import styles from './PolicyQaPage.module.css'

/**
 * 制度问答 (spec §7.3) — session history, follow-up questions with the
 * original context, and interrupted-session recovery via ?session=.
 *
 * Evidence binds to the round that produced it; no confidence percentages.
 */
export function PolicyQaPage() {
  const kbs = useQuery({ queryKey: ['policy-kbs'], queryFn: listPolicyKnowledgeBases })
  const sessions = useQuery({ queryKey: ['policy-sessions'], queryFn: listPolicySessions })
  const [searchParams, setSearchParams] = useSearchParams()
  const [kbId, setKbId] = useState('')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState('')
  const [sources, setSources] = useState<PolicySource[]>([])
  const [phase, setPhase] = useState<'idle' | 'streaming' | 'done' | 'error'>('idle')
  const [error, setError] = useState('')
  const [meta, setMeta] = useState<{ message_id?: string; has_evidence?: boolean }>({})
  const [feedbackSent, setFeedbackSent] = useState(false)
  const [feedbackError, setFeedbackError] = useState('')
  const [answerIncomplete, setAnswerIncomplete] = useState(false)
  const [correcting, setCorrecting] = useState(false)
  const [correction, setCorrection] = useState('')
  const controller = useRef<AbortController | null>(null)

  const selected = kbId || kbs.data?.[0]?.id || ''
  const activeSessionId = sessionId ?? searchParams.get('session')

  // Replay an interrupted/previous session once when opened from history.
  const replay = useQuery({
    queryKey: ['policy-session-messages', activeSessionId],
    queryFn: () => getPolicySessionMessages(activeSessionId!),
    enabled: !!activeSessionId && phase === 'idle' && !answer,
    staleTime: Infinity,
  })
  useEffect(() => {
    if (replay.data) {
      const lastAssistant = [...replay.data.messages].reverse().find(m => m.role === 'assistant')
      if (lastAssistant) {
        setAnswer(lastAssistant.content)
        setSources(lastAssistant.citations)
        // Restore the message identity so feedback works on a resumed answer
        // (audit P1-6: without it the buttons silently did nothing).
        setMeta({
          message_id: lastAssistant.message_id,
          has_evidence: (lastAssistant.citations?.length ?? 0) > 0,
        })
        setAnswerIncomplete(false)
        setPhase('done')
      }
    }
  }, [replay.data])

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
    setFeedbackError('')
    setAnswerIncomplete(false)
    setCorrecting(false)
    setCorrection('')

    try {
      let doneMessageId: string | undefined
      let doneSessionId: string | undefined
      await streamPolicyAnswer(
        { question: question.trim(), kb_id: selected, session_id: activeSessionId ?? undefined },
        next.signal,
        event => {
          if (event.type === 'delta') setAnswer(value => value + event.data.text)
          if (event.type === 'citation') setSources(event.data)
          if (event.type === 'correction') setAnswer(event.data.full_text)
          if (event.type === 'complete') {
            doneMessageId = event.data.message_id
            doneSessionId = event.data.session_id
            setMeta({ message_id: doneMessageId, has_evidence: event.data.has_evidence })
            setPhase('done')
          }
          if (event.type === 'error') {
            setError(event.data.message)
            setPhase('error')
          }
        },
      )
      if (!next.signal.aborted) {
        setPhase(value => (value === 'streaming' ? 'done' : value))
        if (doneSessionId && doneSessionId !== activeSessionId) {
          setSessionId(doneSessionId)
          sessions.refetch()
        }
      }
    } catch (e) {
      if (!next.signal.aborted) {
        setError(e instanceof Error ? e.message : '问答失败')
        setPhase('error')
      }
    }
  }

  function openSession(id: string | null) {
    controller.current?.abort()
    setSessionId(id)
    setAnswer('')
    setSources([])
    setMeta({})
    setPhase('idle')
    setError('')
    // Feedback state belongs to the answer it was given for — never bleed
    // "谢谢，已记录" or a correction draft into a different session.
    setFeedbackSent(false)
    setFeedbackError('')
    setAnswerIncomplete(false)
    setCorrecting(false)
    setCorrection('')
    if (id) setSearchParams({ session: id }, { replace: true })
    else setSearchParams({}, { replace: true })
    if (id) replay.refetch()
  }

  async function sendFeedback(rating: 'up' | 'down', note?: string) {
    if (!meta.message_id) {
      setFeedbackError('当前回答缺少消息标识，无法提交反馈。')
      return
    }
    setFeedbackError('')
    try {
      await submitPolicyFeedback(meta.message_id, rating, note)
      setFeedbackSent(true)
    } catch (e) {
      // Failed feedback must be visible and retryable, never a fake success
      // (audit P1-6: `void`-ed the promise before, showing 已记录 on 500).
      setFeedbackError(e instanceof Error ? `反馈未送达：${e.message}` : '反馈未送达，请再试一次。')
    }
  }

  function submitCorrection() {
    sendFeedback('down', correction.trim() || undefined)
    setCorrecting(false)
  }

  return <main className="page-stack"><header className="page-heading"><div><span className="eyebrow">知识</span><h1>制度问答</h1><p>能问什么：请假、考勤、试用期、报销、调岗等制度问题。答案包含结论、依据原文和下一步。</p></div></header>
    <div className={styles.grid}>
      <aside className={styles.history} aria-label="会话历史">
        <div className={styles.historyHead}><h2>会话历史</h2><button type="button" onClick={() => openSession(null)}>新会话</button></div>
        {sessions.isPending && <AsyncState kind="loading" title="正在读取会话" />}
        {sessions.isError && <AsyncState kind="error" title="会话读取失败" detail={sessions.error.message} action={<button onClick={() => sessions.refetch()}>重试</button>} />}
        {sessions.data && sessions.data.sessions.length === 0 && <p className={styles.historyEmpty}>还没有历史会话。提出第一个问题后，后续可以在此继续追问。</p>}
        {sessions.data && sessions.data.sessions.length > 0 && (
          <ul className={styles.historyList}>
            {sessions.data.sessions.map(item => (
              <li key={item.session_id}>
                <button
                  type="button"
                  className={activeSessionId === item.session_id ? styles.activeSession : ''}
                  onClick={() => openSession(item.session_id)}
                >
                  <span>{item.title}</span>
                  <small>{formatTime(item.updated_at)}</small>
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>
      <div className={styles.workgrid}>
        <section className={styles.question}><form onSubmit={ask}><label>知识库<select value={selected} onChange={e => setKbId(e.target.value)} disabled={kbs.isPending}>{kbs.data?.map(kb => <option key={kb.id} value={kb.id}>{kb.name}</option>)}</select></label><label>问题<textarea rows={4} value={question} onChange={e => setQuestion(e.target.value)} placeholder="例如：请假超过三天需要经过哪些审批？" maxLength={2000} /></label><div className={styles.actions}><small>{question.length} / 2000</small>{phase === 'streaming' ? <span className={styles.streaming}><span className={styles.streamingDot} aria-hidden="true" />正在生成<button type="button" className={styles.stopButton} onClick={() => { controller.current?.abort(); setPhase('idle'); setAnswerIncomplete(true) }}>停止</button></span> : <button className="primary-button" disabled={!selected || !question.trim()}>发送问题</button>}</div></form><p className="truth-note">{activeSessionId ? '当前在历史会话中，追问会携带之前的上下文。' : '可以直接描述员工情况或制度问题；回答后可查看引用来源。'}</p></section>
        <section className={styles.answer} aria-live="polite"><div className={styles.answerHead}><h2>回答</h2></div>{phase === 'idle' && !answer && <AsyncState kind="empty" title="等待问题" detail="选择要查询的制度范围后提问。" />}{phase === 'streaming' && !answer && (
          <div className={styles.retrieving} role="status" aria-busy="true">
            <span className={styles.retrievingSymbol} aria-hidden="true"><span className={styles.retrievingDots}><i /><i /></span></span>
            <div><strong>正在查阅相关制度</strong><p>正在查阅已索引的制度文件。</p></div>
          </div>
        )}{phase === 'error' && <AsyncState kind="error" title="问答未完成" detail={error} />}{answer && <article className={styles.prose}>{answer}</article>}{answerIncomplete && answer && (
          <p className={styles.incompleteNote} role="status">
            回答未完整生成：以上内容可能缺少结论或下一步，请重新提问补全后再采用。
          </p>
        )}{phase === 'done' && <div className={styles.feedback}>{feedbackError && <p role="alert">{feedbackError}</p>}{feedbackSent ? <span className={styles.feedbackThanks}>谢谢，已记录你的判断。</span> : correcting ? <><span>哪里需要改进？（可留空）</span><textarea rows={3} value={correction} onChange={e => setCorrection(e.target.value)} placeholder="例如：正确的处理流程应该是…" /><div><button className="primary-button" onClick={submitCorrection}>提交反馈</button><button className="secondary-button" onClick={() => { setCorrecting(false); setCorrection('') }}>取消</button></div></> : <><span>{meta.has_evidence === false ? '依据较少，建议结合其他来源判断。' : '这个回答有帮助吗？'}</span><button className="primary-button" onClick={() => sendFeedback('up')}>有帮助</button><button className="secondary-button" onClick={() => { setCorrecting(true); setCorrection('') }}>需改进</button></>}</div>}</section>
      </div>
    </div><EvidencePanel sources={sources} asked={phase !== 'idle' || !!answer} pending={phase === 'streaming'} />
  </main>
}

function EvidencePanel({ sources, asked, pending }: { sources: PolicySource[]; asked: boolean; pending: boolean }) {
  if (!asked) return null
  return <section className={styles.evidence}><div className={styles.evidenceHead}><div><span className="eyebrow">依据追溯</span><h2>依据与来源</h2></div></div>{pending && sources.length === 0 ? <p className={styles.empty} role="status">正在查找与你问题最相关的制度原文…</p> : sources.length === 0 ? <p className={styles.empty}>当前资料中找不到依据。可以：换一种问法、上传相关制度，或交给 HR 复核。</p> : <div className={styles.sources}>{sources.map((source, index) => <article key={`${source.document_name}-${index}`}><span>{String(index + 1).padStart(2, '0')}</span><div><h3>{formatDocName(source.document_name)}</h3>{source.section && source.section !== 'unknown' && <small>{source.section}</small>}<p>{source.content_snippet}</p></div></article>)}</div>}</section>
}

function formatDocName(name: string) { return name.replace(/\.(txt|pdf|docx|md)$/i, '') }
function formatTime(value: string | null) { const date = value ? new Date(value) : null; return date && !Number.isNaN(date.getTime()) ? date.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—' }
