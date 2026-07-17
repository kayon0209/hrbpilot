/** HRBP AI Workbench — SSE stream hook for POST-based streaming (Policy QA).

Uses fetch + ReadableStream instead of EventSource, because EventSource
only supports GET requests but our SSE endpoints use POST.
*/

import { useRef, useState, useCallback } from 'react';
import { authStore } from '@/stores/authStore';

const BASE_URL = import.meta.env.VITE_API_URL || '';

interface SSEOptions {
  onChunk?: (text: string) => void;
  onSources?: (sources: unknown[]) => void;
  onDone?: (meta: unknown) => void;
  onError?: (message: string) => void;
  onCorrection?: (fullText: string) => void;
}

interface SSECallbacks {
  start: (body: unknown) => Promise<void>;
  stop: () => void;
  isStreaming: boolean;
}

export function useSSE(path: string, options: SSEOptions = {}): SSECallbacks {
  const [isStreaming, setIsStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const start = useCallback(async (body: unknown) => {
    if (abortRef.current) {
      abortRef.current.abort();
    }

    const controller = new AbortController();
    abortRef.current = controller;
    setIsStreaming(true);

    const token = authStore.getState().accessToken;

    try {
      const response = await fetch(`${BASE_URL}${path}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ message: 'Request failed' }));
        options.onError?.(errorData.message || `HTTP ${response.status}`);
        setIsStreaming(false);
        return;
      }

      const reader = response.body?.getReader();
      if (!reader) {
        options.onError?.('No response body');
        setIsStreaming(false);
        return;
      }

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const dataStr = line.slice(6).trim();
            if (!dataStr) continue;

            try {
              const parsed = JSON.parse(dataStr);
              const eventType = parsed.event;
              const eventData = typeof parsed.data === 'string' ? JSON.parse(parsed.data) : parsed.data;

              switch (eventType) {
                case 'chunk':
                  options.onChunk?.(eventData.text || '');
                  break;
                case 'sources':
                  options.onSources?.(eventData || []);
                  break;
                case 'correction':
                  options.onCorrection?.(eventData.full_text || '');
                  break;
                case 'done':
                  options.onDone?.(eventData);
                  break;
                case 'error':
                  options.onError?.(eventData.message || 'Unknown error');
                  break;
              }
            } catch {
              // Ignore malformed JSON
            }
          }
        }
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name !== 'AbortError') {
        options.onError?.(err.message);
      }
    } finally {
      setIsStreaming(false);
      abortRef.current = null;
    }
  }, [path, options]);

  const stop = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setIsStreaming(false);
  }, []);

  return { start, stop, isStreaming };
}
