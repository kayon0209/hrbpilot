/** HRBP AI Workbench — Evaluation (Redesigned) */

import { useState, useEffect } from 'react';
import { api } from '../../lib/api';
import { ScenarioBarChart, TrendLineChart } from './EvalCharts';

const MOCK_METRICS = [
  { scenario: '制度问答', accuracy: 0.92, latency: 2.3, guardrail_pass: 0.98, human_feedback: 0.85 },
  { scenario: '访谈整理', accuracy: 0.87, latency: 5.1, guardrail_pass: 0.95, human_feedback: 0.78 },
  { scenario: '声音洞察', accuracy: 0.83, latency: 8.2, guardrail_pass: 0.91, human_feedback: 0.72 },
  { scenario: '周报生成', accuracy: 0.89, latency: 3.7, guardrail_pass: 0.96, human_feedback: 0.82 },
  { scenario: '文化传播', accuracy: 0.85, latency: 4.5, guardrail_pass: 0.93, human_feedback: 0.76 },
];

export function EvaluationPage() {
  const [activeScenario, setActiveScenario] = useState('all');
  const [metrics, setMetrics] = useState(MOCK_METRICS);
  const [trendData, setTrendData] = useState<Record<string, Array<{ timestamp: string; score: number }>>>({});

  useEffect(() => {
    api.get<{ scenarios: Array<{ scenario_id: string; metrics: Record<string, { avg: number }> }> }>('/api/eval/metrics')
      .then((res) => {
        if (res.scenarios && res.scenarios.length > 0) {
          const real = res.scenarios.map((s) => ({
            scenario: s.scenario_id,
            accuracy: s.metrics?.citation_accuracy?.avg || 0,
            latency: 0,
            guardrail_pass: 0,
            human_feedback: 0,
          }));
          if (real.length > 0) setMetrics(real);
        }
      }).catch(() => {});
  }, []);

  useEffect(() => {
    // Fetch trend data from eval metrics API
    api.get<{ scenarios: Array<{ scenario_id: string; metrics: Record<string, { avg: number; count: number }> }> }>('/api/eval/metrics')
      .then((res) => {
        const trends: Record<string, Array<{ timestamp: string; score: number }>> = {};
        (res.scenarios || []).forEach((s) => {
          if (s.metrics && s.metrics.citation_accuracy) {
            trends[s.scenario_id] = [
              { timestamp: new Date().toISOString(), score: s.metrics.citation_accuracy.avg }
            ];
          }
        });
        if (Object.keys(trends).length > 0) setTrendData(trends);
      })
      .catch(() => {});

    // Also try per-scenario trends
    MOCK_METRICS.forEach((m) => {
      api.get<{ data: Array<{ timestamp: string; score: number }> }>(`/api/eval/metrics/${m.scenario}/trend?metric=citation_accuracy&days=7`)
        .then((res) => {
          if (res.data && res.data.length > 0) {
            setTrendData((prev) => ({ ...prev, [m.scenario]: res.data }));
          }
        })
        .catch(() => {});
    });
  }, []);

  const filtered = activeScenario === 'all' ? metrics : MOCK_METRICS.filter((m) => m.scenario === activeScenario);

  return (
    <div className="flex-1 overflow-auto px-8 py-6 bg-neutral-50">
      <h1 className="text-page-title text-neutral-800 mb-1">评测看板</h1>
      <p className="text-body text-neutral-400 mb-6">监控各场景的准确率、延迟、护栏通过率等关键指标</p>

      {/* Scenario filter */}
      <div className="flex gap-2 mb-6">
        <button onClick={() => setActiveScenario('all')} className={`text-caption px-4 py-1.5 rounded-md transition-all duration-fast ${activeScenario === 'all' ? 'bg-primary-500 text-white font-medium' : 'text-neutral-500 hover:bg-primary-50'}`}>
          全部
        </button>
        {MOCK_METRICS.map((m) => (
          <button key={m.scenario} onClick={() => setActiveScenario(m.scenario)} className={`text-caption px-4 py-1.5 rounded-md transition-all duration-fast ${activeScenario === m.scenario ? 'bg-primary-500 text-white font-medium' : 'text-neutral-500 hover:bg-primary-50'}`}>
            {m.scenario}
          </button>
        ))}
      </div>

      {/* Summary KPIs */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        {[
          { label: '平均准确率', value: `${(filtered.reduce((s, m) => s + m.accuracy, 0) / filtered.length * 100).toFixed(0)}%`, color: 'bg-primary-500' },
          { label: '平均延迟', value: `${(filtered.reduce((s, m) => s + m.latency, 0) / filtered.length).toFixed(1)}s`, color: 'bg-accent-500' },
          { label: '护栏通过率', value: `${(filtered.reduce((s, m) => s + m.guardrail_pass, 0) / filtered.length * 100).toFixed(0)}%`, color: 'bg-emerald-500' },
          { label: '人工好评率', value: `${(filtered.reduce((s, m) => s + m.human_feedback, 0) / filtered.length * 100).toFixed(0)}%`, color: 'bg-warning-500' },
        ].map((kpi) => (
          <div key={kpi.label} className="card flex items-center gap-4">
            <div className={`w-3 h-3 rounded-full ${kpi.color}`} />
            <div>
              <div className="text-caption text-neutral-400 mb-1">{kpi.label}</div>
              <div className="metric-value text-neutral-800">{kpi.value}</div>
            </div>
          </div>
        ))}
      </div>

      {/* Detail table */}
      <div className="card overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-neutral-200 bg-neutral-50">
              <th className="text-left px-5 py-3 text-caption text-neutral-500 font-medium">场景</th>
              <th className="text-left px-5 py-3 text-caption text-neutral-500 font-medium">准确率</th>
              <th className="text-left px-5 py-3 text-caption text-neutral-500 font-medium">延迟(s)</th>
              <th className="text-left px-5 py-3 text-caption text-neutral-500 font-medium">护栏通过率</th>
              <th className="text-left px-5 py-3 text-caption text-neutral-500 font-medium">人工好评率</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((m) => (
              <tr key={m.scenario} className="border-b border-neutral-100 hover:bg-neutral-50 transition-colors">
                <td className="px-5 py-3 text-body-sm text-neutral-700 font-medium">{m.scenario}</td>
                <td className="px-5 py-3 text-body-sm text-neutral-600">{(m.accuracy * 100).toFixed(0)}%</td>
                <td className="px-5 py-3 text-body-sm text-neutral-600">{m.latency}</td>
                <td className="px-5 py-3 text-body-sm text-neutral-600">{(m.guardrail_pass * 100).toFixed(0)}%</td>
                <td className="px-5 py-3 text-body-sm text-neutral-600">{(m.human_feedback * 100).toFixed(0)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Charts */}
      <h2 className="text-section-title text-neutral-600 mt-8 mb-4">场景质量对比</h2>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-8">
        <div className="card">
          <ScenarioBarChart metrics={filtered} />
        </div>
        <div className="card">
          <TrendLineChart data={trendData} />
        </div>
      </div>
    </div>
  );
}
