/** HRBP AI Workbench — Sidebar (Redesigned)
 *
 * Modern sidebar with SVG icons, scene accent colors,
 * improved spacing and hover states.
 */

import { useAuth } from '@/hooks/useAuth';
import { SCENES, MANAGEMENT_PAGES, ROLE_LABELS, Role } from '@/lib/constants';
import { useNavigate, useLocation } from 'react-router-dom';
import clsx from 'clsx';

interface NavItem {
  label: string;
  route: string;
  icon: string;
  requiredRole: Role;
  color?: string;
}

const SCENE_ITEMS: NavItem[] = Object.values(SCENES).map((s) => ({
  label: s.label,
  route: s.route,
  icon: s.icon,
  requiredRole: s.requiredRole as Role,
  color: s.color,
}));

const MGMT_ITEMS: NavItem[] = Object.values(MANAGEMENT_PAGES).map((p) => ({
  label: p.label,
  route: p.route,
  icon: p.icon,
  requiredRole: p.requiredRole as Role,
}));

// Color mapping must match `color` values in constants.ts SCENES
const SCENE_COLORS: Record<string, { active: string; hover: string }> = {
  indigo: { active: 'bg-indigo-50 text-indigo-600', hover: 'hover:bg-indigo-50/50' },
  sky: { active: 'bg-sky-50 text-sky-600', hover: 'hover:bg-sky-50/50' },
  emerald: { active: 'bg-emerald-50 text-emerald-600', hover: 'hover:bg-emerald-50/50' },
  amber: { active: 'bg-amber-50 text-amber-600', hover: 'hover:bg-amber-50/50' },
  rose: { active: 'bg-rose-50 text-rose-600', hover: 'hover:bg-rose-50/50' },
};

const ICON_SVG: Record<string, React.ReactNode> = {
  Grid: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" />
    </svg>
  ),
  BookOpen: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" /><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
    </svg>
  ),
  FileText: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" /><line x1="16" y1="13" x2="8" y2="13" /><line x1="16" y1="17" x2="8" y2="17" />
    </svg>
  ),
  BarChart3: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="20" x2="18" y2="10" /><line x1="12" y1="20" x2="12" y2="4" /><line x1="6" y1="20" x2="6" y2="14" />
    </svg>
  ),
  Report: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
      <rect x="8" y="2" width="8" height="4" rx="1" /><line x1="12" y1="11" x2="12" y2="17" /><line x1="9" y1="14" x2="15" y2="14" />
    </svg>
  ),
  PenTool: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 19l7-7 3 3-7 7-3-3z" /><path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z" />
      <path d="M2 2l7.586 7.586" /><circle cx="11" cy="11" r="2" />
    </svg>
  ),
  Database: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
      <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
    </svg>
  ),
  LineChart: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
    </svg>
  ),
  Settings: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  ),
};

export function Sidebar() {
  const { role, checkAccess } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const visibleScenes = SCENE_ITEMS.filter((item) => checkAccess(item.requiredRole));
  const visibleMgmt = MGMT_ITEMS.filter((item) => checkAccess(item.requiredRole));

  return (
    <aside
      className="flex flex-col bg-white border-r border-neutral-200 h-screen overflow-y-auto"
      style={{ width: 'var(--sidebar-width)' }}
    >
      {/* Brand */}
      <div className="px-5 py-5 border-b border-neutral-100">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-md bg-[#E85D04] flex items-center justify-center shadow-sm">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <rect x="2" y="3" width="8" height="18" rx="1.5" fill="white" opacity="0.95"/>
              <rect x="13" y="3" width="8" height="8" rx="1.5" fill="white" opacity="0.6"/>
              <rect x="13" y="14" width="8" height="7" rx="1.5" fill="white" opacity="0.6"/>
            </svg>
          </div>
          <div>
            <div className="text-card-title text-primary-600 font-semibold tracking-tight">HRBP AI</div>
            <div className="text-caption text-neutral-400">工作台</div>
          </div>
        </div>
      </div>

      {/* Dashboard */}
      <div className="px-3 pt-4">
        <NavItemComponent
          label="仪表盘"
          icon="Grid"
          isActive={location.pathname === '/dashboard'}
          onClick={() => navigate('/dashboard')}
        />
      </div>

      {/* Scene nav */}
      <div className="px-3 pt-5">
        <div className="px-3 pb-2 text-caption text-neutral-400 font-medium uppercase tracking-wider">场景</div>
        {visibleScenes.map((item) => (
          <NavItemComponent
            key={item.route}
            label={item.label}
            icon={item.icon}
            color={item.color}
            isActive={location.pathname.startsWith(item.route)}
            onClick={() => navigate(item.route)}
          />
        ))}
      </div>

      {/* Management nav */}
      {visibleMgmt.length > 0 && (
        <div className="px-3 pt-5">
          <div className="px-3 pb-2 text-caption text-neutral-400 font-medium uppercase tracking-wider">管理</div>
          {visibleMgmt.map((item) => (
            <NavItemComponent
              key={item.route}
              label={item.label}
              icon={item.icon}
              isActive={location.pathname.startsWith(item.route)}
              onClick={() => navigate(item.route)}
            />
          ))}
        </div>
      )}

      {/* User footer */}
      <div className="mt-auto px-5 py-4 border-t border-neutral-100">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-primary-100 text-primary-600 flex items-center justify-center text-caption font-semibold">
            {role?.charAt(0).toUpperCase() || 'U'}
          </div>
          <div>
            <div className="text-body-sm text-neutral-700 font-medium">{ROLE_LABELS[role] || '员工'}</div>
            <div className="text-caption text-neutral-400">{role}</div>
          </div>
        </div>
      </div>
    </aside>
  );
}

function NavItemComponent({
  label,
  icon,
  color,
  isActive,
  onClick,
}: {
  label: string;
  icon: string;
  color?: string;
  isActive: boolean;
  onClick: () => void;
}) {
  const sceneStyle = color ? SCENE_COLORS[color] : undefined;

  return (
    <button
      onClick={onClick}
      className={clsx(
        'nav-item',
        isActive
          ? sceneStyle?.active || 'bg-primary-50 text-primary-600 font-medium'
          : clsx('text-neutral-600', sceneStyle?.hover || 'hover:bg-neutral-50')
      )}
    >
      <span className="flex-shrink-0">{ICON_SVG[icon] || ICON_SVG.Grid}</span>
      <span>{label}</span>
    </button>
  );
}
