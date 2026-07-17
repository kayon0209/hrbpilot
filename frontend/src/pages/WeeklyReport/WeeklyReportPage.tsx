import { useState } from 'react';
import { api } from '../../lib/api';
import { toast } from '@/components/ui/Toast';

interface ProgressItem { item: string; source: string; status: string; }
interface RiskItem { risk: string; severity: string; owner: string; action: string; }
interface PlanItem { task: string; priority: string; deadline: string; }
interface WeeklyReport {
  period: string; summary: string; progress: ProgressItem[];
  risks: RiskItem[]; plan: PlanItem[]; data_sources: string[];
  confidence: number; has_evidence: boolean;
}

const DATA_SOURCES = [
  { id: 'interview', label: '访谈记录', desc: '本周员工访谈数据', checked: true },
  { id: 'personnel', label: '人员变动', desc: '入职/离职/调岗信息', checked: false },
  { id: 'recruiting', label: '招聘进度', desc: '在招岗位和开放情况', checked: false },
  { id: 'key_events', label: '关键事项', desc: '本周重要事件和会议纪要', checked: false },
];

const STATUS_STYLES: Record<string, string> = {
  '已完成': 'bg-emerald-50 text-emerald-600 border-emerald-200',
  '进行中': 'bg-accent-50 text-accent-600 border-accent-200',
  '待启动': 'bg-neutral-50 text-neutral-600 border-neutral-200',
};
const SEVERITY_STYLES: Record<string, string> = {
  'HIGH': 'bg-danger-50 text-danger-600 border-danger-200',
  'MEDIUM': 'bg-warning-50 text-warning-600 border-warning-200',
  'LOW': 'bg-emerald-50 text-emerald-600 border-emerald-200',
};

export function WeeklyReportPage() {
  const [period, setPeriod] = useState('2026-W28');
  const [sources, setSources] = useState(DATA_SOURCES);
  const [notes, setNotes] = useState('');
  const [result, setResult] = useState<WeeklyReport | null>(null);
  const [reportId, setReportId] = useState<string | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggleSource = (id: string) => {
    setSources((prev) => prev.map((s) => (s.id === id ? { ...s, checked: !s.checked } : s)));
  };

  const selectedSources = sources.filter((s) => s.checked).map((s) => s.id);

  const handleGenerate = async () => {
    if (selectedSources.length === 0) {
      toast.warning('请至少选择一个数据源');
      return;
    }
    setIsGenerating(true); setError(null);
    try {
      const response = await api.post<{ report_id: string; report: WeeklyReport }>(
        '/api/weekly-report/generate',
        { period, source_ids: selectedSources, draft_mode: true, notes: notes }
      );
      setResult(response.report); setReportId(response.report_id);
      toast.success('周报生成成功');
    } catch (err) {
      setError('生成失败: ' + (err as Error).message);
      toast.error('生成失败');
    } finally { setIsGenerating(false); }
  };

  const handlePublish = async () => {
    if (!reportId) return;
    try {
      await api.post('/api/weekly-report/save', { report_id: reportId, action: 'publish' });
      toast.success('周报已发布');
    } catch (err) { toast.error('发布失败'); }
  };

  const handleRegenerate = () => { setResult(null); setReportId(null); };

  return (
    <div className="flex h-[calc(100vh-56px)]">
      {/* Left: Data source panel */}
      <div className="w-[340px] shrink-0 border-r border-neutral-200 bg-white flex flex-col overflow-auto">
        <div className="px-5 py-4 border-b border-neutral-100">
          <h2 className="text-section-title text-neutral-700 mb-1">数据源</h2>
          <p className="text-caption text-neutral-400">勾选本周要包含的内容</p>
        </div>
        <div className="p-4 flex flex-col gap-3 flex-1">
          <div className="space-y-2">
            {sources.map((src) => (
              <label key={src.id} className={'flex items-start gap-3 px-4 py-3 rounded-lg border cursor-pointer transition-all ' + (src.checked ? 'border-accent-400 bg-accent-50' : 'border-neutral-200 hover:border-neutral-300')}>
                <input type="checkbox" checked={src.checked} onChange={() => toggleSource(src.id)} className="mt-0.5 accent-[#E85D04]" />
                <div>
                  <div className="text-sm text-neutral-700 font-medium">{src.label}</div>
                  <div className="text-xs text-neutral-400">{src.desc}</div>
                </div>
              </label>
            ))}
          </div>

          <div className="mt-2">
            <label className="text-xs text-neutral-500 block mb-1.5">报告周期</label>
            <input value={period} onChange={(e) => setPeriod(e.target.value)} className="input-base w-full" placeholder="如 2026-W28" />
          </div>

          <div>
            <label className="text-xs text-neutral-500 block mb-1.5">补充备注</label>
            <textarea value={notes} onChange={(e) => setNotes(e.target.value)} className="input-base w-full h-20 resize-none" placeholder="手动补充本周重点..." />
          </div>

          <button onClick={handleGenerate} disabled={isGenerating || selectedSources.length === 0} className="btn-primary w-full mt-auto">
            {isGenerating ? '生成中...' : '生成周报草稿'}
          </button>
        </div>
      </div>

      {/* Right: Report draft */}
      <div className="flex-1 flex flex-col overflow-auto bg-neutral-50">
        <div className="p-6">
          {isGenerating && (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-caption text-neutral-400">
                <span className="animate-pulse text-primary-500">{'\u25cf'}</span> 正在聚合数据并生成周报...
              </div>
              <div className="progress-track"><div className="progress-fill" style={{ width: '60%' }} /></div>
            </div>
          )}
          {error && <div className="px-4 py-3 rounded-md bg-danger-50 border border-danger-200 text-sm text-danger-600 mb-4">{error}</div>}

          {!result && !isGenerating && !error && (
            <div className="flex items-center justify-center h-64">
              <div className="text-center">
                <div className="w-14 h-14 mx-auto rounded-2xl bg-amber-50 flex items-center justify-center mb-4">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                       strokeWidth="1.8" className="text-amber-500" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
                    <rect x="8" y="2" width="8" height="4" rx="1" />
                    <line x1="12" y1="11" x2="12" y2="17" /><line x1="9" y1="14" x2="15" y2="14" />
                  </svg>
                </div>
                <div className="text-section-title text-neutral-700 mb-2">选择数据源开始生成</div>
                <div className="text-body text-neutral-400">在左侧勾选数据来源并设置报告周期，一键生成 HRBP 周报</div>
              </div>
            </div>
          )}

          {result && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-section-title text-neutral-700">{result.period} HRBP 周报</h2>
                  <div className="text-caption text-neutral-400">数据来源: {result.data_sources?.join(', ') || '未知'} {'\u00b7'} 置信度: {(result.confidence * 100).toFixed(0)}%</div>
                </div>
                <div className="flex gap-2">
                  <button onClick={handleRegenerate} className="btn-secondary text-xs px-3 py-1.5 h-auto">重新生成</button>
                  <button onClick={handlePublish} className="btn-primary text-xs px-3 py-1.5 h-auto">发布周报</button>
                </div>
              </div>

              <div className="card">
                <h3 className="text-card-title text-neutral-600 mb-2">本周摘要</h3>
                <div className="text-body text-neutral-700">{result.summary}</div>
              </div>

              {result.progress.length > 0 && (
                <div className="card">
                  <h3 className="text-card-title text-neutral-600 mb-3">本周进展</h3>
                  <div className="space-y-2">
                    {result.progress.map((p, i) => (
                      <div key={i} className="flex items-center gap-3 px-4 py-2 rounded-md bg-neutral-50">
                        <span className={'badge border ' + (STATUS_STYLES[p.status] || STATUS_STYLES['进行中'])}>{p.status}</span>
                        <span className="text-sm text-neutral-700 flex-1">{p.item}</span>
                        <span className="text-xs text-neutral-400">{p.source}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {result.risks.length > 0 && (
                <div className="card border-warning-200">
                  <h3 className="text-card-title text-warning-600 mb-3">风险预警</h3>
                  <div className="space-y-2">
                    {result.risks.map((r, i) => (
                      <div key={i} className="px-4 py-2 rounded-md bg-warning-50 border border-warning-100">
                        <div className="flex items-center gap-2">
                          <span className={'badge border ' + (SEVERITY_STYLES[r.severity] || SEVERITY_STYLES.MEDIUM)}>{r.severity}</span>
                          <span className="text-sm text-neutral-700">{r.risk}</span>
                        </div>
                        <div className="text-xs text-neutral-500 mt-1">跟进: {r.owner} {'\u00b7'} 应对: {r.action}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {result.plan.length > 0 && (
                <div className="card">
                  <h3 className="text-card-title text-neutral-600 mb-3">下周计划</h3>
                  <div className="space-y-2">
                    {result.plan.map((p, i) => (
                      <div key={i} className="flex items-center gap-3 px-4 py-2 rounded-md bg-neutral-50">
                        <span className="text-accent-500 font-semibold text-sm">{i + 1}.</span>
                        <span className="text-sm text-neutral-700 flex-1">{p.task}</span>
                        <span className="text-xs text-neutral-400">{p.priority} {'\u00b7'} {p.deadline}</span>
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
