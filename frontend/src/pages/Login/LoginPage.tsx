/** HRBP AI Workbench — LoginPage (Redesigned)
 *
 * Modern split-screen login: dark branding panel on left,
 * clean form on right. Dev quick-login below form.
 */

import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth';

interface DevUser {
  email: string;
  name: string;
  role: string;
  password: string;
}

export function LoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [devUsers, setDevUsers] = useState<DevUser[]>([]);
  const { login } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    fetch('/api/auth/dev-users')
      .then((r) => r.json())
      .then((data) => { if (data.users) setDevUsers(data.users); })
      .catch(() => {});
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError('');

    const success = await login(email, password);
    setIsLoading(false);

    if (success) {
      navigate('/dashboard');
    } else {
      setError('邮箱或密码错误，请重试');
    }
  };

  const quickLogin = async (user: DevUser) => {
    setIsLoading(true);
    setError('');
    const success = await login(user.email, user.password);
    setIsLoading(false);
    if (success) navigate('/dashboard');
    else setError('登录失败');
  };

  const ROLE_BADGES: Record<string, { bg: string; text: string; border: string }> = {
    admin: { bg: 'bg-primary-50', text: 'text-primary-600', border: 'border-primary-200' },
    hr_manager: { bg: 'bg-accent-50', text: 'text-accent-600', border: 'border-accent-200' },
    hrbp: { bg: 'bg-emerald-50', text: 'text-emerald-600', border: 'border-emerald-200' },
    employee: { bg: 'bg-neutral-50', text: 'text-neutral-600', border: 'border-neutral-200' },
  };

  return (
    <div className="flex min-h-screen">
      {/* Left: Branding panel */}
      <div className="hidden lg:flex lg:w-[45%] bg-neutral-800 flex-col items-center justify-center px-12 relative overflow-hidden">
        {/* Decorative circles */}
        <div className="absolute top-20 left-20 w-32 h-32 rounded-full bg-primary-500/10" />
        <div className="absolute bottom-40 right-16 w-24 h-24 rounded-full bg-accent-500/10" />
        <div className="absolute top-60 right-32 w-16 h-16 rounded-full bg-emerald-500/10" />

        <div className="relative z-10 max-w-sm">
          {/* Logo */}
          <div className="w-12 h-12 rounded-xl bg-[#E85D04] flex items-center justify-center mb-6 shadow-md">
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none">
              <rect x="2" y="3" width="8" height="18" rx="1.5" fill="white" opacity="0.95"/>
              <rect x="13" y="3" width="8" height="8" rx="1.5" fill="white" opacity="0.6"/>
              <rect x="13" y="14" width="8" height="7" rx="1.5" fill="white" opacity="0.6"/>
            </svg>
          </div>

          <h1 className="text-display text-white mb-3">HRBP AI</h1>
          <h2 className="text-section-title text-neutral-400 mb-6">智能工作台</h2>

          <div className="w-12 h-1 rounded-full bg-primary-500 mb-6" />

          <div className="space-y-3 text-body-sm text-neutral-300">
            <p>5 个 HR 场景 · RAG 护栏底座</p>
            <p>制度问答 · 访谈整理 · 声音洞察</p>
            <p>周报生成 · 文化传播</p>
          </div>

          <div className="mt-10 flex gap-4">
            <div className="px-4 py-2 rounded-md bg-primary-500/20 text-primary-300 text-caption">
              RBAC 4 角色
            </div>
            <div className="px-4 py-2 rounded-md bg-emerald-500/20 text-emerald-300 text-caption">
              多 LLM 支持
            </div>
          </div>
        </div>
      </div>

      {/* Right: Login form */}
      <div className="flex-1 flex flex-col items-center justify-center px-6 bg-neutral-50">
        <div className="w-full max-w-sm">
          {/* Logo for mobile */}
          <div className="lg:hidden flex items-center gap-3 mb-6 justify-center">
            <div className="w-8 h-8 rounded-lg bg-primary-500 flex items-center justify-center">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="white">
                <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" stroke="white" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </div>
            <div className="text-card-title text-primary-600 font-semibold">HRBP AI 工作台</div>
          </div>

          {/* Form */}
          <div className="card">
            <h2 className="text-section-title text-neutral-800 mb-1">登录</h2>
            <p className="text-caption text-neutral-400 mb-5">访问您的场景工作台</p>

            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="text-body-sm text-neutral-500 block mb-1.5">邮箱</label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="input-base w-full"
                  placeholder="your@email.com"
                  required
                />
              </div>

              <div>
                <label className="text-body-sm text-neutral-500 block mb-1.5">密码</label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="input-base w-full"
                  placeholder="enter password"
                  required
                />
              </div>

              {error && (
                <div className="text-body-sm text-danger-600 bg-danger-50 rounded-md px-4 py-3 border border-danger-200">
                  {error}
                </div>
              )}

              <button
                type="submit"
                disabled={isLoading}
                className="btn-primary w-full"
              >
                {isLoading ? '登录中...' : '登录'}
              </button>
            </form>
          </div>

          {/* Dev quick login */}
          {devUsers.length > 0 && (
            <div className="mt-5">
              <div className="text-caption text-neutral-400 mb-3 text-center">快速登录 (开发模式)</div>
              <div className="grid grid-cols-2 gap-3">
                {devUsers.map((u) => {
                  const badge = ROLE_BADGES[u.role] || ROLE_BADGES.employee;
                  return (
                    <button
                      key={u.email}
                      onClick={() => quickLogin(u)}
                      disabled={isLoading}
                      className={`px-4 py-2.5 rounded-md border ${badge.border} ${badge.bg} ${badge.text} text-body-sm font-medium hover:opacity-80 transition-opacity disabled:opacity-50`}
                    >
                      {u.name}
                    </button>
                  );
                })}
              </div>
              <div className="text-center text-caption text-neutral-300 mt-3">密码统一为 123456</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
