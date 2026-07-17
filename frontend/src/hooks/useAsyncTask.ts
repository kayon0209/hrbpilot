/** HRBP AI Workbench — async task polling hook (manual + auto modes) */

import { useEffect, useState, useCallback, useRef } from 'react';
import { apiClient } from '@/lib/api';

export interface AsyncTaskStatus {
  status: 'pending' | 'running' | 'completed' | 'failed' | string;
  progress?: number;
  error?: string;
  result?: unknown;
}

interface AsyncTask {
  id: string;
  type: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  progress: number;
  result_json: string | null;
  error_message: string | null;
}

export function useAsyncTask(taskId: string | null = null, pollInterval = 2000) {
  const [task, setTask] = useState<AsyncTask | null>(null);
  const [taskStatus, setTaskStatus] = useState<AsyncTaskStatus | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const intervalRef = useRef<number | null>(null);

  const stopPolling = useCallback(() => {
    if (intervalRef.current) {
      window.clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    setIsLoading(false);
  }, []);

  const fetchTask = useCallback(async () => {
    if (!taskId) return;
    setIsLoading(true);
    try {
      const data = await apiClient.get<AsyncTask>(`/api/async-tasks/${taskId}`);
      setTask(data);
      setTaskStatus({
        status: data.status,
        progress: data.progress,
        error: data.error_message || undefined,
        result: data.result_json ? JSON.parse(data.result_json) : undefined,
      });
      if (data.status === 'completed' || data.status === 'failed') {
        stopPolling();
      }
    } catch {
      // Error — will retry on next poll
    } finally {
      setIsLoading(false);
    }
  }, [taskId, stopPolling]);

  const startPolling = useCallback(
    (pathOrTaskId: string, onUpdate?: (status: AsyncTaskStatus) => void) => {
      stopPolling();
      setIsLoading(true);

      const isPath = pathOrTaskId.startsWith('/');
      const pollUrl = isPath ? pathOrTaskId : `/api/async-tasks/${pathOrTaskId}`;

      const tick = async () => {
        try {
          const data = await apiClient.get<AsyncTaskStatus | AsyncTask>(pollUrl);
          const status: AsyncTaskStatus =
            'status' in data && typeof data.status === 'string'
              ? (data as AsyncTaskStatus)
              : {
                  status: (data as AsyncTask).status,
                  progress: (data as AsyncTask).progress,
                  error: (data as AsyncTask).error_message || undefined,
                  result: (data as AsyncTask).result_json
                    ? JSON.parse((data as AsyncTask).result_json!)
                    : undefined,
                };

          setTaskStatus(status);
          onUpdate?.(status);

          if (status.status === 'completed' || status.status === 'failed') {
            stopPolling();
          }
        } catch {
          // Retry on next poll
        }
      };

      tick();
      intervalRef.current = window.setInterval(tick, pollInterval);
    },
    [pollInterval, stopPolling]
  );

  useEffect(() => {
    if (!taskId) return;
    fetchTask();
    intervalRef.current = window.setInterval(fetchTask, pollInterval);
    return () => {
      if (intervalRef.current) window.clearInterval(intervalRef.current);
    };
  }, [taskId, pollInterval, fetchTask]);

  return { task, taskStatus, isLoading, startPolling, stopPolling, refetch: fetchTask };
}
