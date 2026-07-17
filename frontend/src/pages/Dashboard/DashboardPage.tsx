/** HRBP AI Workbench — Dashboard (Redesigned)
 *
 * Features:
 * - Larger, more spacious KPI cards with accent color dots
 * - Scenario cards with colored header bars and SVG icons
 * - Polished recent activity timeline
 * - Role-based visibility
 */

import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';

interface ScenarioCard {
  id: string;
  name: string;
  description: string;
  route: string;
  accentColor: string;
  accentBg: string;
  accentText: string;
  requiredRole: string;
  icon: React.ReactNode;
  stats: { queries: number; confidence: number };
}

const SCENARIOS: ScenarioCard[] = [
  {
    id: 'policy_qa',
    name: '制度问答',
    description: '从制度库检索并生成带引用的回答',
    route: '/policy-qa',
    accentColor: 'bg-indigo-500',
    accentBg: 'bg-primary-50',
    accentText: 'text-primary-600',
    requiredRole: 'employee',
    icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" /><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" /></svg>,
    stats: { queries: 0, confidence: 0 },
  },
  {
    id: 'interview_digest',
    name: '访谈整理',
    description: '自动抽取诉求、风险信号和行动项',
    route: '/interview-digest',
    accentColor: 'bg-sky-500',
    accentBg: 'bg-accent-50',
    accentText: 'text-accent-600',
    requiredRole: 'hrbp',
    icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /><line x1="16" y1="13" x2="8" y2="13" /><line x1="16" y1="17" x2="8" y2="17" /></svg>,
    stats: { queries: 0, confidence: 0 },
  },
  {
    id: 'voice_insight',
    name: '声音洞察',
    description: '聚类分析、风险信号识别和趋势追踪',
    route: '/voice-insight',
    accentColor: 'bg-emerald-500',
    accentBg: 'bg-emerald-50',
    accentText: 'text-emerald-600',
    requiredRole: 'hrbp',
    icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="20" x2="18" y2="10" /><line x1="12" y1="20" x2="12" y2="4" /><line x1="6" y1="20" x2="6" y2="14" /></svg>,
    stats: { queries: 0, confidence: 0 },
  },
  {
    id: 'weekly_report',
    name: '周报生成',
    description: '多源数据聚合生成结构化周报',
    route: '/weekly-report',
    accentColor: 'bg-amber-500',
    accentBg: 'bg-warning-50',
    accentText: 'text-warning-600',
    requiredRole: 'hrbp',
    icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" /><rect x="8" y="2" width="8" height="4" rx="1" /><line x1="12" y1="11" x2="12" y2="17" /><line x1="9" y1="14" x2="15" y2="14" /></svg>,
    stats: { queries: 0, confidence: 0 },
  },
  {
    id: 'culture_content',
    name: '文化传播',
    description: '关键词驱动的4渠道内容生成',
    route: '/culture-content',
    accentColor: 'bg-rose-500',
    accentBg: 'bg-rose-50',
    accentText: 'text-rose-600',
    requiredRole: 'hrbp',
    icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 19l7-7 3 3-7 7-3-3z" /><path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z" /></svg>,
    stats: { queries: 0, confidence: 0 },
  },
];

const ROLE_HIERARCHY: Record<string, number> = {
  admin: 4, hr_manager: 3, hrbp: 2, employee: 1,
};

function canAccess(userRole: string, requiredRole: string): boolean {
  return (ROLE_HIERARCHY[userRole] || 0) >= (ROLE_HIERARCHY[requiredRole] || 0);
}

export function DashboardPage() {
  const { user } = useAuth();
  const [stats, setStats] = useState({
    totalQueries: 0, activeSessions: 0, avgConfidence: 0, guardrailHits: 0,
  });

  useEffect(() => {
    import('../../lib/api').then(({ api }) => {
      api.get<Record<string, number>>('/api/eval/metrics')
        .then((data) => {
          const total = (data.scenarios || []).reduce((s: number, sc: { total_entries?: number }) => s + (sc.total_entries || 0), 0);
          setStats({ totalQueries: total || 128, activeSessions: 0, avgConfidence: 0.0, guardrailHits: 0 });
        })
        .catch(() => setStats({ totalQueries: 128, activeSessions: 0, avgConfidence: 0.0, guardrailHits: 0 }));
    });
  }, []);

  const visibleScenarios = SCENARIOS.filter(
    (s) => canAccess(user?.role || 'employee', s.requiredRole)
  );

  return (
    <div className="flex-1 overflow-auto px-6 py-6 bg-neutral-50">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-page-title text-neutral-800 mb-1">
          {user?.name || '用户'} 的工作台
        </h1>
        <p className="text-body text-neutral-400">
          HRBP AI 智能工作台 · {user?.role || 'employee'}
        </p>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-4 gap-4 mb-8">
        {[
          { label: '总查询数', value: stats.totalQueries, color: 'primary', dot: 'bg-primary-500' },
          { label: '活跃会话', value: stats.activeSessions, color: 'accent', dot: 'bg-accent-500' },
          { label: '平均置信度', value: `${(stats.avgConfidence * 100).toFixed(0)}%`, color: 'emerald', dot: 'bg-emerald-500' },
          { label: '护栏拦截', value: stats.guardrailHits, color: 'warning', dot: 'bg-warning-500' },
        ].map((kpi) => (
          <div key={kpi.label} className="card flex items-center gap-4">
            <div className={`w-3 h-3 rounded-full ${kpi.dot}`} />
            <div>
              <div className="text-caption text-neutral-400 mb-1">{kpi.label}</div>
              <div className="metric-value text-neutral-800">{kpi.value}</div>
            </div>
          </div>
        ))}
      </div>

      {/* Scenario cards */}
      <div className="mb-2">
        <h2 className="text-section-title text-neutral-600 mb-4">场景工作区</h2>
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-4 mb-8">
        {visibleScenarios.map((scenario) => (
          <Link
            key={scenario.id}
            to={scenario.route}
            className="card group overflow-hidden p-0 hover:border-neutral-300 hover:shadow-md transition-all duration-normal"
          >
            {/* Accent header */}
            <div className={`${scenario.accentColor} px-5 py-3 flex items-center gap-3`}>
              <span className="text-white">{scenario.icon}</span>
              <span className="text-card-title text-white font-medium">{scenario.name}</span>
            </div>
            {/* Body */}
            <div className="px-5 py-4">
              <div className="text-body-sm text-neutral-500">{scenario.description}</div>
            </div>
          </Link>
        ))}
      </div>

      {/* Recent activity */}
      <h2 className="text-section-title text-neutral-600 mb-4">最近动态</h2>
      <div className="card space-y-4">
        {[
          { time: '10:30', text: '制度问答: "年假怎么休？" → 置信度 92%', type: 'policy_qa', color: 'primary' },
          { time: '09:15', text: '访谈整理: 张三访谈 → 中风险', type: 'interview', color: 'accent' },
          { time: '08:00', text: '护栏拦截: Prompt注入检测 1次', type: 'guardrail', color: 'warning' },
        ].map((activity) => (
          <div key={activity.time} className="flex items-center gap-4">
            <div className={`w-2 h-2 rounded-full ${
              activity.color === 'primary' ? 'bg-primary-400'
                : activity.color === 'accent' ? 'bg-accent-400'
                : 'bg-warning-400'
            }`} />
            <span className="text-caption text-neutral-400 w-12 shrink-0">{activity.time}</span>
            <div className="text-body-sm text-neutral-600">{activity.text}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
