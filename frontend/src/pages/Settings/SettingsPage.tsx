/** HRBP AI Workbench — Settings Page (Redesigned)
 *
 * Clean settings with section navigation, role matrix,
 * guardrail toggles, LLM provider management.
 */

import { useState, useEffect } from 'react';
import { useAuth } from '../../hooks/useAuth';
import { api } from '../../lib/api';

interface LLMProvider { id: string; label: string; model: string; active: boolean; api_key_masked: string; }
interface ProvidersResponse { providers: LLMProvider[]; active: string; active_model: string; }

const SECTIONS = [
  { id: 'general', label: '通用设置', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></svg> },
  { id: 'roles', label: '角色管理', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></svg> },
  { id: 'guardrails', label: '护栏规则', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /></svg> },
  { id: 'llm', label: 'LLM 配置', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" /></svg> },
  { id: 'notifications', label: '通知设置', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" /><path d="M13.73 21a2 2 0 0 1-3.46 0" /></svg> },
];

const ROLE_MATRIX = [
  { role: 'Employee', policy_qa: '✓', other: '—', manage: '—' },
  { role: 'HRBP', policy_qa: '✓', other: '✓', manage: '—' },
  { role: 'HR Manager', policy_qa: '✓', other: '✓', manage: '✓' },
  { role: 'Admin', policy_qa: '✓', other: '✓', manage: '✓' },
];

const PROVIDER_STYLES: Record<string, { bg: string; text: string; border: string }> = {
  zhipu: { bg: 'bg-accent-50', text: 'text-accent-600', border: 'border-accent-200' },
  deepseek: { bg: 'bg-primary-50', text: 'text-primary-600', border: 'border-primary-200' },
  openai: { bg: 'bg-emerald-50', text: 'text-emerald-600', border: 'border-emerald-200' },
};

export function SettingsPage() {
  const [activeSection, setActiveSection] = useState('general');
  const [guardrails, setGuardrails] = useState({
    pii_detection: true, prompt_injection: true, factuality_check: true, toxicity_detection: true, confidence_threshold: 0.65,
  });
  const [providers, setProviders] = useState<LLMProvider[]>([]);
  const [activeProvider, setActiveProvider] = useState('');
  const [activeModel, setActiveModel] = useState('');
  const [testResult, setTestResult] = useState<{ status: string; response?: string; error?: string } | null>(null);
  const [isSwitching, setIsSwitching] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const { user } = useAuth();

  const fetchProviders = async () => {
    try {
      const data = await api.get<ProvidersResponse>('/api/settings/llm-provider');
      setProviders(data.providers); setActiveProvider(data.active); setActiveModel(data.active_model);
    } catch (e) { console.error('Failed to load providers', e); }
  };

  useEffect(() => { if (activeSection === 'llm') fetchProviders(); }, [activeSection]);

  const switchProvider = async (providerId: string) => {
    setIsSwitching(true); setTestResult(null);
    try {
      const data = await api.post<{ active: string; active_model: string }>('/api/settings/llm-provider', { provider: providerId });
      setActiveProvider(data.active); setActiveModel(data.active_model); await fetchProviders();
    } catch { console.error('Switch failed'); } finally { setIsSwitching(false); }
  };

  const testProvider = async () => {
    setIsTesting(true); setTestResult(null);
    try {
      const data = await api.get<{ status: string; response?: string; error?: string; model: string; provider: string }>('/api/settings/llm-provider/test');
      setTestResult({ status: data.status, response: data.response, error: data.error });
    } catch (e: unknown) { setTestResult({ status: 'error', error: e instanceof Error ? e.message : '请求失败' }); } finally { setIsTesting(false); }
  };

  return (
    <div className="flex h-[calc(100vh-56px)]">
      {/* Section nav */}
      <div className="w-56 border-r border-neutral-200 bg-white py-5 px-3">
        {SECTIONS.map((s) => (
          <button key={s.id} onClick={() => setActiveSection(s.id)}
            className={`nav-item mb-1 ${activeSection === s.id ? 'active' : ''}`}>
            {s.icon}
            <span>{s.label}</span>
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto px-8 py-6">
        {activeSection === 'general' && (
          <div>
            <h2 className="text-section-title text-neutral-700 mb-5">通用设置</h2>
            <div className="space-y-4">
              <div className="card">
                <div className="text-card-title text-neutral-600 mb-2">租户信息</div>
                <div className="text-body-sm text-neutral-500">租户 ID: default</div>
                <div className="text-body-sm text-neutral-500">环境: development</div>
              </div>
              <div className="card">
                <div className="text-card-title text-neutral-600 mb-2">当前用户</div>
                <div className="text-body-sm text-neutral-500">{user?.name || '未知'} · 角色: {user?.role || '未知'}</div>
              </div>
            </div>
          </div>
        )}

        {activeSection === 'roles' && (
          <div>
            <h2 className="text-section-title text-neutral-700 mb-5">角色权限矩阵</h2>
            <div className="card overflow-hidden">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-neutral-200 bg-neutral-50">
                    <th className="text-left px-5 py-3 text-caption text-neutral-500 font-medium">角色</th>
                    <th className="text-left px-5 py-3 text-caption text-neutral-500 font-medium">制度问答</th>
                    <th className="text-left px-5 py-3 text-caption text-neutral-500 font-medium">其他场景</th>
                    <th className="text-left px-5 py-3 text-caption text-neutral-500 font-medium">管理权限</th>
                  </tr>
                </thead>
                <tbody>
                  {ROLE_MATRIX.map((r) => (
                    <tr key={r.role} className="border-b border-neutral-100 hover:bg-neutral-50 transition-colors">
                      <td className="px-5 py-3 text-body-sm text-neutral-700 font-medium">{r.role}</td>
                      <td className="px-5 py-3 text-body-sm text-neutral-600">{r.policy_qa}</td>
                      <td className="px-5 py-3 text-body-sm text-neutral-600">{r.other}</td>
                      <td className="px-5 py-3 text-body-sm text-neutral-600">{r.manage}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {activeSection === 'guardrails' && (
          <div>
            <h2 className="text-section-title text-neutral-700 mb-5">护栏规则配置</h2>
            <div className="space-y-3">
              {[
                { key: 'pii_detection', label: 'PII 检测', desc: '检测并脱敏姓名、手机号、身份证等个人信息' },
                { key: 'prompt_injection', label: 'Prompt 注入检测', desc: '拦截恶意 Prompt 注入攻击' },
                { key: 'factuality_check', label: '事实性校验', desc: '校验回答是否与知识库内容一致' },
                { key: 'toxicity_detection', label: '毒性检测', desc: '检测输出中的有害或不当内容' },
              ].map((rule) => (
                <div key={rule.key} className="card flex items-center justify-between">
                  <div>
                    <div className="text-card-title text-neutral-600">{rule.label}</div>
                    <div className="text-caption text-neutral-400">{rule.desc}</div>
                  </div>
                  <button
                    onClick={() => setGuardrails((prev) => ({ ...prev, [rule.key]: !prev[rule.key as keyof typeof prev] }))}
                    className={`toggle ${guardrails[rule.key as keyof typeof guardrails] ? 'active' : ''}`}
                  >
                    <div className="toggle-dot" />
                  </button>
                </div>
              ))}
              <div className="card">
                <div className="text-card-title text-neutral-600">置信度阈值</div>
                <div className="text-caption text-neutral-400 mt-1">当前: {guardrails.confidence_threshold} · 低于此值的回答将标记为"无依据"</div>
                <input type="range" min="0.3" max="0.9" step="0.05" value={guardrails.confidence_threshold}
                  onChange={(e) => setGuardrails((prev) => ({ ...prev, confidence_threshold: parseFloat(e.target.value) }))}
                  className="w-full mt-3 accent-primary-500" />
              </div>
            </div>
          </div>
        )}

        {activeSection === 'llm' && (
          <div>
            <h2 className="text-section-title text-neutral-700 mb-5">LLM Provider 配置</h2>

            <div className="card bg-primary-50 border-primary-200 mb-5">
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-caption text-neutral-500">当前激活</div>
                  <div className="text-card-title text-neutral-800 mt-1 font-semibold">
                    {providers.find(p => p.id === activeProvider)?.label || activeProvider}
                  </div>
                  <div className="text-caption text-neutral-500 mt-0.5">Model: {activeModel}</div>
                </div>
                <button onClick={testProvider} disabled={isTesting} className="btn-secondary">
                  {isTesting ? '测试中...' : '测试连接'}
                </button>
              </div>
              {testResult && (
                <div className={`mt-3 px-4 py-2 rounded-md text-caption ${
                  testResult.status === 'ok' ? 'bg-emerald-50 text-emerald-600' : 'bg-danger-50 text-danger-600'
                }`}>
                  {testResult.status === 'ok' ? `连接成功 · 响应: "${testResult.response}"` : `连接失败 · ${testResult.error}`}
                </div>
              )}
            </div>

            <div className="text-card-title text-neutral-600 mb-3">可用 Provider ({providers.length})</div>
            {providers.length === 0 ? (
              <div className="card text-center text-caption text-neutral-400 py-10">加载中...</div>
            ) : (
              <div className="space-y-3">
                {providers.map((p) => {
                  const style = PROVIDER_STYLES[p.id] || { bg: 'bg-neutral-50', text: 'text-neutral-600', border: 'border-neutral-200' };
                  return (
                    <div key={p.id} className={`card ${p.active ? 'bg-primary-50 border-primary-200' : ''}`}>
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                          <span className={`badge border ${style.border} ${style.bg} ${style.text}`}>{p.id.toUpperCase()}</span>
                          <div>
                            <div className="text-body-sm text-neutral-800 font-medium">{p.label}</div>
                            <div className="text-caption text-neutral-400">{p.model} · Key: {p.api_key_masked}</div>
                          </div>
                        </div>
                        {p.active ? (
                          <span className="badge bg-primary-500 text-white">当前使用</span>
                        ) : (
                          <button onClick={() => switchProvider(p.id)} disabled={isSwitching} className="btn-secondary text-caption px-3 py-1.5 h-auto">
                            {isSwitching ? '切换中...' : '切换'}
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            <div className="card bg-warning-50 border-warning-200 mt-5">
              <div className="text-caption text-warning-700">
                切换 Provider 后即时生效，无需重启。当主 Provider 不可用时可快速切换到备用。
              </div>
            </div>
          </div>
        )}

        {activeSection === 'notifications' && (
          <div>
            <h2 className="text-section-title text-neutral-700 mb-5">通知设置</h2>
            <div className="space-y-3">
              {[
                { label: '护栏拦截通知', desc: '当护栏拦截请求时发送通知' },
                { label: '风险信号通知', desc: '访谈分析发现高风险时发送通知' },
                { label: '分析完成通知', desc: '异步任务完成时发送通知' },
                { label: 'Token 超预算通知', desc: 'LLM token 消耗超过预算时发送告警' },
              ].map((n) => (
                <div key={n.label} className="card flex items-center justify-between">
                  <div>
                    <div className="text-card-title text-neutral-600">{n.label}</div>
                    <div className="text-caption text-neutral-400">{n.desc}</div>
                  </div>
                  <div className="toggle">
                    <div className="toggle-dot" />
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
