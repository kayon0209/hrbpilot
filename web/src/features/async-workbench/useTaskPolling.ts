import { useQuery } from '@tanstack/react-query'
import type { TaskProgress } from '../../api/async-scenarios'

export type TaskPhase = 'idle' | 'queued' | 'processing' | 'completed' | 'error'

/**
 * Poll an async analysis task and expose HONEST stage semantics.
 *
 * Scenario analysis is a single LLM generation with no measurable steps, so
 * there is no real percentage to show (spec §9.1 mode `none`): we surface the
 * backend stage word — 排队中 / 正在分析 / 已完成 / 失败 — never a fake number.
 *
 * Polling errors never stop silently forever: a transient failure (worker
 * restart, momentary 401 refresh) keeps the UI in an explicit "读取失败可重试"
 * state instead of pretending to analyze for eternity.
 */
export function useTaskPolling<T>(taskId: string | null, getProgress: (id: string) => Promise<TaskProgress>, getResult: (id: string) => Promise<T>) {
  const progress = useQuery({
    queryKey: ['task-progress', taskId],
    queryFn: () => getProgress(taskId!),
    enabled: !!taskId,
    refetchInterval: query => {
      if (query.state.status === 'error') {
        // Back off, but keep watching: the task itself may still complete.
        return 5000
      }
      const status = query.state.data?.status
      return status === 'completed' || status === 'failed' || status === 'error' ? false : 2000
    },
  })
  const status = progress.data?.status
  const completed = status === 'completed'
  const result = useQuery({ queryKey: ['task-result', taskId], queryFn: () => getResult(taskId!), enabled: !!taskId && completed, retry: 1 })
  const phase: TaskPhase = !taskId
    ? 'idle'
    : progress.isError || status === 'failed' || status === 'error'
      ? 'error'
      : completed && result.data
        ? 'completed'
        : status === 'pending'
          ? 'queued'
          : 'processing'
  const stageLabel = phase === 'queued' ? '排队中' : phase === 'processing' ? '正在分析' : ''
  return {
    phase,
    stageLabel,
    result: result.data,
    error: progress.data?.error ?? result.error?.message ?? (progress.isError ? '暂时无法读取任务状态，正在自动重试。' : null),
    retry: () => { progress.refetch(); if (completed) result.refetch() },
  }
}
