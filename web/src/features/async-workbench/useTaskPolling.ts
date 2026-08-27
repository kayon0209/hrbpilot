import { useQuery } from '@tanstack/react-query'
import type { TaskProgress } from '../../api/async-scenarios'

export function useTaskPolling<T>(taskId: string | null, getProgress: (id: string) => Promise<TaskProgress>, getResult: (id: string) => Promise<T>) {
  const progress = useQuery({ queryKey: ['task-progress', taskId], queryFn: () => getProgress(taskId!), enabled: !!taskId, refetchInterval: query => { const status = query.state.data?.status; return status === 'completed' || status === 'failed' || status === 'error' ? false : 2000 } })
  const completed = progress.data?.status === 'completed'
  const result = useQuery({ queryKey: ['task-result', taskId], queryFn: () => getResult(taskId!), enabled: !!taskId && completed, retry: 1 })
  const phase = !taskId ? 'idle' : progress.isError || progress.data?.status === 'failed' || progress.data?.status === 'error' ? 'error' : completed && result.data ? 'completed' : 'processing'
  return { phase, progress: progress.data?.progress ?? 0, result: result.data, error: progress.error?.message ?? progress.data?.error ?? result.error?.message, retry: () => { progress.refetch(); if (completed) result.refetch() } }
}
