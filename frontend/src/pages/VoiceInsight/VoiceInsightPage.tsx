import { useState } from 'react';
import { api } from '../../lib/api';
import { useAsyncTask } from '../../hooks/useAsyncTask';
import { ClusterBubbleChart, TrendBarChart } from './VoiceCharts';

interface Cluster { label: string; demand_count: number; demands: string[]; severity: string; }
interface RiskSignal { signal: string; severity: string; source_ids: string[]; trend: string; }
interface Trend { topic: string; direction: string; confidence: number; evidence: string; }
interface InsightReport { clusters: Cluster[]; risk_signals: RiskSignal[]; trends: Trend[]; summary: string; confidence: number; has_evidence: boolean; }
interface UploadedDoc { filename: string; content_type: string; text_length: number; content: string; }

const SEVERITY_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  HIGH: { bg: 'bg-danger-50', text: 'text-danger-600', border: 'border-danger-200' },
  MEDIUM: { bg: 'bg-warning-50', text: 'text-warning-600', border: 'border-warning-200' },
  LOW: { bg: 'bg-emerald-50', text: 'text-emerald-600', border: 'border-emerald-200' },
};
const TREND_ARROWS: Record<string, string> = { '上升': '↑', '稳定': '→', '下降': '↓' };

export function VoiceInsightPage() {
  const [uploadedDocs, setUploadedDocs] = useState<UploadedDoc[]>([]);
  const [result, setResult] = useState<InsightReport | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { taskStatus, startPolling, stopPolling } = useAsyncTask();

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    setError(null);
    try {
      for (const file of Array.from(files)) {
        const formData = new FormData();
        formData.append('file', file);
        const response = await api.post<UploadedDoc>('/api/interview-digest/upload', formData);
        setUploadedDocs((prev) => [...prev, response]);
      }
    } catch (err) {
      setError('上传失败: ' + (err as Error).message);
    }
  };

  const handleAnalyze = async () => {
    const docs = [...uploadedDocs];
    if (docs.length === 0) return;
    setIsAnalyzing(true); setError(null); setResult(null);
    try {
      const allContent = docs.map((d) => d.content).join('\n---\n');
      const response = await api.post<{ task_id: string; status: string }>(
        '/api/voice-insight/analyze',
        { document_ids: [], content: allContent }
      );
      startPolling('/api/voice-insight/progress/' + response.task_id, (status) => {
        if (status.status === 'completed') {
          api.get<InsightReport>('/api/voice-insight/report/' + response.task_id)
            .then((res) => { setResult(res); setIsAnalyzing(false); stopPolling(); })
            .catch(() => { setError('获取报告失败'); setIsAnalyzing(false); stopPolling(); });
        } else if (status.status === 'failed') {
          setError('分析失败'); setIsAnalyzing(false); stopPolling();
        }
      });
    } catch (err) {
      setError('启动失败: ' + (err as Error).message); setIsAnalyzing(false);
    }
  };

  const removeDoc = (i: number) => setUploadedDocs((prev) => prev.filter((_, idx) => idx !== i));

  return (
    <div className="flex h-[calc(100vh-56px)]">
      {/* Left: Upload panel */}
      <div className="w-[340px] shrink-0 border-r border-neutral-200 bg-white flex flex-col overflow-auto">
        <div className="px-5 py-4 border-b border-neutral-100">
          <h2 className="text-section-title text-neutral-700 mb-1">导入数据</h2>
          <p className="text-caption text-neutral-400">上传员工声音文件 (txt/docx/pdf)</p>
        </div>
        <div className="p-4 flex flex-col gap-3">
          <label className="border-2 border-dashed border-neutral-300 rounded-lg p-5 text-center cursor-pointer hover:border-emerald-400 hover:bg-emerald-50 transition-all">
            <input type="file" accept=".docx,.pdf,.txt" multiple onChange={handleFileUpload} disabled={isAnalyzing} className="hidden" />
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
                 className="mx-auto mb-2 text-neutral-400" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" y1="3" x2="12" y2="15" />
            </svg>
            <span className="text-sm text-neutral-500">点击上传文件</span>
          </label>

          {uploadedDocs.map((doc, i) => (
            <div key={i} className="flex items-center gap-2 px-3 py-2 rounded-md bg-neutral-50 border border-neutral-100">
              <span className="text-xs text-neutral-400 truncate flex-1">{doc.filename}</span>
              <span className="text-xs text-neutral-300">{doc.text_length}chars</span>
              <button onClick={() => removeDoc(i)} className="text-xs text-neutral-400 hover:text-danger-500">x</button>
            </div>
          ))}

          <button onClick={handleAnalyze} disabled={isAnalyzing || uploadedDocs.length === 0} className="btn-primary w-full mt-1">
            {isAnalyzing ? '分析中...' : `开始分析 (${uploadedDocs.length}个文件)`}
          </button>
        </div>
      </div>

      {/* Right: Results */}
      <div className="flex-1 flex flex-col overflow-auto bg-neutral-50">
        <div className="p-6">
          {isAnalyzing && (
            <div className="space-y-3">
              <div className="flex items-center gap-2 mb-2">
                <span className="animate-pulse text-emerald-500">{'●'}</span>
                <span className="text-caption text-neutral-400">正在分析声音数据...</span>
              </div>
              <div className="progress-track">
                <div className="progress-fill bg-emerald-500" style={{ width: ((taskStatus?.progress || 0) * 100) + '%' }} />
              </div>
            </div>
          )}
          {error && <div className="px-4 py-3 rounded-md bg-danger-50 border border-danger-200 text-sm text-danger-600">{error}</div>}

          {!result && !isAnalyzing && !error && (
            <div className="flex items-center justify-center h-64">
              <div className="text-center">
                <div className="w-14 h-14 mx-auto rounded-2xl bg-emerald-50 flex items-center justify-center mb-4">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                       strokeWidth="1.8" className="text-emerald-500" strokeLinecap="round" strokeLinejoin="round">
                    <line x1="18" y1="20" x2="18" y2="10" /><line x1="12" y1="20" x2="12" y2="4" /><line x1="6" y1="20" x2="6" y2="14" />
                  </svg>
                </div>
                <div className="text-section-title text-neutral-700 mb-2">导入文件开始分析</div>
                <div className="text-body text-neutral-400">上传员工声音数据，AI 自动聚类并识别风险和趋势</div>
              </div>
            </div>
          )}

          {result && (
            <div className="space-y-4">
              <div className="card">
                <h3 className="text-card-title text-neutral-600 mb-2">洞察摘要</h3>
                <div className="text-body text-neutral-700">{result.summary}</div>
                <div className="text-caption text-neutral-400 mt-2">置信度: {(result.confidence * 100).toFixed(0)}%</div>
              </div>

              {(result.clusters.length > 0 || result.trends.length > 0) && (
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                  {result.clusters.length > 0 && (
                    <div className="card">
                      <h3 className="text-card-title text-neutral-600 mb-3">诉求聚类分布</h3>
                      <ClusterBubbleChart clusters={result.clusters} />
                    </div>
                  )}
                  {result.trends.length > 0 && (
                    <div className="card">
                      <h3 className="text-card-title text-neutral-600 mb-3">趋势变化</h3>
                      <TrendBarChart trends={result.trends} />
                    </div>
                  )}
                </div>
              )}

              {result.clusters.length > 0 && (
                <div className="card">
                  <h3 className="text-card-title text-neutral-600 mb-3">诉求聚类详情</h3>
                  <div className="space-y-3">
                    {result.clusters.map((c, i) => {
                      const sc = SEVERITY_COLORS[c.severity] || SEVERITY_COLORS.LOW;
                      return (
                        <div key={i} className="px-4 py-3 rounded-md bg-neutral-50 border border-neutral-200">
                          <div className="flex items-center gap-2 mb-1">
                            <span className={'badge border ' + sc.border + ' ' + sc.bg + ' ' + sc.text}>{c.severity}</span>
                            <span className="text-card-title text-neutral-700">{c.label}</span>
                            <span className="text-caption text-neutral-400">({c.demand_count}条诉求)</span>
                          </div>
                          <div className="text-caption text-neutral-500 space-y-0.5">
                            {c.demands.slice(0, 3).map((d, di) => <div key={di}>{'·'} {d}</div>)}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {result.risk_signals.length > 0 && (
                <div className="card border-warning-200">
                  <h3 className="text-card-title text-warning-600 mb-3">风险信号</h3>
                  <div className="space-y-2">
                    {result.risk_signals.map((r, i) => {
                      const sc = SEVERITY_COLORS[r.severity] || SEVERITY_COLORS.MEDIUM;
                      return (
                        <div key={i} className="flex items-center gap-3 px-4 py-2 rounded-md bg-warning-50 border border-warning-100">
                          <span className={'badge border ' + sc.border + ' ' + sc.bg + ' ' + sc.text}>{r.severity}</span>
                          <span className="text-body-sm text-neutral-700 flex-1">{r.signal}</span>
                          <span className="text-caption text-neutral-400">{(TREND_ARROWS[r.trend] || '→') + ' ' + r.trend}</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {result.trends.length > 0 && (
                <div className="card">
                  <h3 className="text-card-title text-neutral-600 mb-3">趋势详情</h3>
                  <div className="space-y-2">
                    {result.trends.map((t, i) => (
                      <div key={i} className="flex items-center gap-4 px-4 py-2 rounded-md bg-neutral-50">
                        <span className="text-body-sm text-primary-500 font-semibold">{TREND_ARROWS[t.direction] || '→'}</span>
                        <div className="flex-1">
                          <div className="text-body-sm text-neutral-700">{t.topic}: {t.direction}</div>
                          <div className="text-caption text-neutral-400">{t.evidence}</div>
                        </div>
                        <span className="text-caption text-primary-600 font-medium">{(t.confidence * 100).toFixed(0)}%</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
