/** HRBP AI Workbench — InterviewDigest with sub-components, Skeleton, Toast, dual-column layout. */

import { useState, useRef } from 'react';
import { api } from '../../lib/api';
import { useAsyncTask } from '../../hooks/useAsyncTask';
import { toast } from '@/components/ui/Toast';
import { Skeleton } from '@/components/ui/Skeleton';
import { UploadZone } from './UploadZone';
import { FileList } from './FileList';
import { ResultDetail } from './ResultDetail';

interface Demand { demand: string; category: string; urgency: string; }
interface ActionItem { action: string; owner: string; deadline: string; }
interface DigestResult {
  employee_demands: Demand[]; risk_level: string; risk_signals: string[];
  action_items: ActionItem[]; suggested_owner: string; summary: string;
  confidence: number; has_evidence: boolean;
}
interface UploadedDoc { filename: string; content_type: string; text_length: number; content: string; }

export function InterviewDigestPage() {
  const [uploadedDocs, setUploadedDocs] = useState<UploadedDoc[]>([]);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [result, setResult] = useState<DigestResult | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { taskStatus, startPolling, stopPolling } = useAsyncTask();

  const handleFileUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setError(null);
    try {
      for (const file of Array.from(files)) {
        const formData = new FormData();
        formData.append('file', file);
        const response = await api.post<UploadedDoc>('/api/interview-digest/upload', formData);
        setUploadedDocs((prev) => [...prev, response]);
      }
      toast.success('文件上传成功');
    } catch (err) {
      toast.error('上传失败');
      setError(`上传失败: ${(err as Error).message}`);
    }
  };

  const handleAnalyze = async () => {
    if (uploadedDocs.length === 0) return;
    setIsAnalyzing(true); setError(null); setResult(null);
    try {
      const lastDoc = uploadedDocs[uploadedDocs.length - 1];
      const startResponse = await api.post<{ task_id: string; status: string }>(
        '/api/interview-digest/analyze', { content: lastDoc.content }
      );
      setTaskId(startResponse.task_id);
      startPolling(`/api/interview-digest/progress/${startResponse.task_id}`, (status) => {
        if (status.status === 'completed') {
          api.get<DigestResult>(`/api/interview-digest/result/${startResponse.task_id}`)
            .then((res) => { setResult(res); setIsAnalyzing(false); stopPolling(); toast.success('分析完成'); })
            .catch((err) => { setError(`获取结果失败: ${(err as Error).message}`); setIsAnalyzing(false); stopPolling(); });
        } else if (status.status === 'failed') {
          setError('分析失败'); setIsAnalyzing(false); stopPolling(); toast.error('分析失败');
        }
      });
    } catch (err) {
      setError(`启动分析失败: ${(err as Error).message}`); setIsAnalyzing(false); toast.error('启动分析失败');
    }
  };

  return (
    <div className="flex h-[calc(100vh-56px)]">
      <div className="w-[360px] shrink-0 border-r border-neutral-200 bg-white flex flex-col overflow-auto">
        <div className="px-6 py-5 border-b border-neutral-100">
          <h2 className="text-section-title text-neutral-700 mb-1">文件上传</h2>
          <p className="text-caption text-neutral-400">上传访谈记录，自动分析</p>
        </div>
        <div className="p-4">
          <UploadZone onUpload={handleFileUpload} disabled={isAnalyzing} />
          <div className="mt-4">
            <FileList docs={uploadedDocs} onRemove={(i) => setUploadedDocs((prev) => prev.filter((_, idx) => idx !== i))} />
          </div>
          {uploadedDocs.length > 0 && (
            <button onClick={handleAnalyze} disabled={isAnalyzing} className="btn-primary w-full mt-3">
              {isAnalyzing ? '分析中...' : '开始分析'}
            </button>
          )}
        </div>
      </div>
      <div className="flex-1 flex flex-col overflow-auto bg-neutral-50">
        <div className="p-6">
          {isAnalyzing && (
            <div className="space-y-4">
              <div className="flex items-center gap-3 mb-2">
                <span className="animate-pulse text-primary-500">●</span>
                <span className="text-caption text-neutral-400">正在分析访谈内容...</span>
              </div>
              <div className="progress-track">
                <div className="progress-fill" style={{ width: `${(taskStatus?.progress || 0) * 100}%` }} />
              </div>
              <Skeleton variant="rectangular" height={80} />
              <Skeleton variant="text" width="60%" />
              <Skeleton variant="text" />
              <Skeleton variant="text" />
              <Skeleton variant="rectangular" height={120} />
            </div>
          )}
          {error && (
            <div className="px-4 py-3 rounded-md bg-danger-50 border border-danger-200 text-body-sm text-danger-600">
              {error}
            </div>
          )}
          {!result && !isAnalyzing && !error && (
            <div className="flex items-center justify-center h-64">
              <div className="text-center">
                <div className="w-14 h-14 mx-auto rounded-2xl bg-accent-50 flex items-center justify-center mb-4">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                       strokeWidth="1.8" className="text-accent-500" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                    <polyline points="14 2 14 8 20 8" />
                    <line x1="16" y1="13" x2="8" y2="13" />
                  </svg>
                </div>
                <div className="text-section-title text-neutral-700 mb-2">上传文件开始分析</div>
                <div className="text-body text-neutral-400">上传访谈记录 (docx/pdf/txt)，AI 自动分析</div>
              </div>
            </div>
          )}
          {result && <ResultDetail result={result} />}
        </div>
      </div>
      <div className="w-72 shrink-0 border-l border-neutral-200 bg-white p-5 flex flex-col">
        <h3 className="text-card-title text-neutral-600 mb-4">历史记录</h3>
        <div className="text-caption text-neutral-400 mt-8 text-center">暂无历史记录</div>
      </div>
    </div>
  );
}
