/** HRBP AI Workbench — TopBar (Redesigned)
 *
 * Clean top bar with search input, user avatar,
 * notification badge, and smooth transitions.
 */

import { useAuth } from '@/hooks/useAuth';
import { useNavigate } from 'react-router-dom';

export function TopBar() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  return (
    <header
      className="flex items-center justify-between px-6 bg-white border-b border-neutral-200"
      style={{ height: 'var(--topbar-height)' }}
    >
      {/* Search */}
      <div className="flex items-center gap-3">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="text-neutral-400">
          <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        <div className="w-56 h-9 rounded-md bg-neutral-50 border border-neutral-200 flex items-center px-3 text-caption text-neutral-400 transition-colors focus-within:border-primary-300 focus-within:bg-white">
          <input
            type="text"
            placeholder="搜索功能、制度..."
            className="w-full bg-transparent outline-none text-body-sm text-neutral-700 placeholder:text-neutral-400"
          />
        </div>
      </div>

      {/* Right side */}
      <div className="flex items-center gap-4">
        {/* Notification */}
        <button className="relative p-2 rounded-md text-neutral-500 hover:bg-neutral-50 hover:text-neutral-700 transition-colors">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" /><path d="M13.73 21a2 2 0 0 1-3.46 0" />
          </svg>
          <span className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full bg-danger-500" />
        </button>

        {/* User info */}
        {user && (
          <div className="flex items-center gap-3 pl-3 border-l border-neutral-200">
            <div className="w-8 h-8 rounded-full bg-primary-100 text-primary-600 flex items-center justify-center text-caption font-semibold">
              {user.name?.charAt(0) || 'A'}
            </div>
            <div className="text-body-sm text-neutral-700 font-medium">{user.name}</div>
            <button
              onClick={() => { logout(); navigate('/login'); }}
              className="text-caption text-neutral-400 hover:text-danger-500 transition-colors px-2 py-1 rounded-sm hover:bg-danger-50"
            >
              退出
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
