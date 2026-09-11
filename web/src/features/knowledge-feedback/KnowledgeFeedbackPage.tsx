import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AsyncState } from '../../components/AsyncState'
import { listCandidates, decideCandidate, type FeedbackCandidate } from '../../api/knowledge-feedback'
import styles from './KnowledgeFeedbackPage.module.css'

/**
 * 知识与反馈 (spec §7.7) — the manager's judgment queue.
 *
 * Candidates are system SUGGESTIONS (no-evidence questions, down-rated
 * answers, repeated themes); they never become knowledge-gap conclusions on
 * their own. Every candidate needs a human action with a reason.
 */
export function KnowledgeFeedbackPage() {
  const queryClient = useQueryClient()
  const candidates = useQuery({ queryKey: ['knowledge-feedback'], queryFn: listCandidates })
  const decide = useMutation({
    mutationFn: decideCandidate,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['knowledge-feedback'] }),
  })

  return (
    <main className="page-stack">
      <header className="page-heading">
        <div>
          <span className="eyebrow">团队治理</span>
          <h1>知识与反馈</h1>
          <p>系统从真实使用中归纳的候选：无证据问题、低评价纠正、高频未确认主题。每条都需要你的判断，确认前不会成为知识缺口结论。</p>
        </div>
      </header>

      {candidates.isPending && <AsyncState kind="loading" title="正在归纳候选" detail="读取问答使用信号。" />}
      {candidates.isError && (
        <AsyncState
          kind="error"
          title="候选读取失败"
          detail={candidates.error.message}
          action={<button onClick={() => candidates.refetch()}>重试</button>}
        />
      )}
      {candidates.data && candidates.data.candidates.length === 0 && (
        <section className="panel">
          <h2>当前没有待判断事项</h2>
          <p>没有发现无证据问题或低评价回答。新的使用信号出现后会归纳到这里。</p>
        </section>
      )}
      {candidates.data && candidates.data.candidates.length > 0 && (
        <section className="panel">
          <h2>待你判断</h2>
          <div className={styles.list}>
            {candidates.data.candidates.map(candidate => (
              <CandidateCard key={candidate.candidate_id} candidate={candidate} onDecide={decide.mutate} pending={decide.isPending && decide.variables?.candidate_id === candidate.candidate_id} />
            ))}
          </div>
        </section>
      )}
      {decide.isError && <AsyncState kind="error" title="处理未保存" detail={decide.error.message} />}
    </main>
  )
}

function CandidateCard({ candidate, onDecide, pending }: {
  candidate: FeedbackCandidate
  onDecide: (body: { candidate_id: string; decision: 'confirm' | 'assign' | 'reject'; reason?: string; assignee?: string }) => void
  pending: boolean
}) {
  const decided = candidate.status !== 'open'
  return (
    <article className={`${styles.card} ${styles[`card${candidate.status.charAt(0).toUpperCase() + candidate.status.slice(1)}`] ?? ''}`}>
      <div className={styles.cardHead}>
        <span className={`${styles.source} ${styles[`source${candidate.source_type.charAt(0).toUpperCase() + candidate.source_type.slice(1).replace(/_([a-z])/g, (_, c) => c.toUpperCase())}`] ?? ''}`}>{candidate.source_label}</span>
        <span className={styles.occurrences}>{candidate.occurrences} 次</span>
      </div>
      <h3>&ldquo;{candidate.question}&rdquo;</h3>
      {candidate.evidence_summary && <p className={styles.evidence}>{candidate.evidence_summary}</p>}
      {decided ? (
        <p className={styles.decided}>
          已{candidate.status === 'confirmed' ? '确认为知识缺口' : candidate.status === 'rejected' ? '驳回' : `指派给 ${candidate.assignee}`}
          {candidate.handled_reason ? `：${candidate.handled_reason}` : ''}
        </p>
      ) : (
        <div className={styles.actions} aria-label="处理动作">
          <button className="primary-button" disabled={pending} onClick={() => onDecide({ candidate_id: candidate.candidate_id, decision: 'confirm', reason: '确认为知识缺口，待补充制度' })}>
            确认缺口
          </button>
          <button className="secondary-button" disabled={pending} onClick={() => { const assignee = window.prompt('指派给谁？（例如：制度负责人姓名）'); if (assignee) onDecide({ candidate_id: candidate.candidate_id, decision: 'assign', assignee, reason: `指派给 ${assignee} 处理` }) }}>
            指派处理
          </button>
          <button className="secondary-button" disabled={pending} onClick={() => { const reason = window.prompt('驳回原因（会记录在案）') ?? ''; if (reason) onDecide({ candidate_id: candidate.candidate_id, decision: 'reject', reason }) }}>
            驳回
          </button>
        </div>
      )}
    </article>
  )
}
