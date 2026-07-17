/** HRBP AI Workbench — Toast notification component.
 *
 * Simple, zero-dependency toast system.
 * Usage:
 *   import { toast } from '@/components/ui/Toast';
 *   toast.show('操作成功', 'success');
 */

import { useState, useCallback, createContext, useContext } from 'react';
import clsx from 'clsx';

type ToastType = 'info' | 'success' | 'warning' | 'error';

interface ToastItem { id: string; message: string; type: ToastType; }

const typeStyles: Record<ToastType, string> = {
  info: 'bg-primary-500 text-white',
  success: 'bg-emerald-500 text-white',
  warning: 'bg-warning-500 text-white',
  error: 'bg-danger-500 text-white',
};

interface ToastCtx { show: (message: string, type?: ToastType, duration?: number) => void; }
const ToastContext = createContext<ToastCtx>({ show: () => {} });
export const useToast = () => useContext(ToastContext);

export const toast = {
  _ctx: null as ToastCtx | null,
  _ensure() { if (!this._ctx) throw new Error('ToastProvider not mounted'); },
  show(msg: string, type: ToastType = 'info', dur = 4000) { this._ensure(); this._ctx!.show(msg, type, dur); },
  success(msg: string) { this.show(msg, 'success'); },
  error(msg: string) { this.show(msg, 'error'); },
  warning(msg: string) { this.show(msg, 'warning'); },
  info(msg: string) { this.show(msg, 'info'); },
};

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const show = useCallback((message: string, type: ToastType = 'info', duration = 4000) => {
    const id = Date.now().toString(36) + Math.random().toString(36).slice(2);
    setItems((prev) => [...prev, { id, message, type }]);
    setTimeout(() => setItems((prev) => prev.filter((t) => t.id !== id)), duration);
  }, []);

  // Wire the singleton
  toast._ctx = { show };

  return (
    <ToastContext.Provider value={{ show }}>
      {children}
      <div className="fixed bottom-6 right-6 z-50 flex flex-col gap-2 pointer-events-none">
        {items.map((t) => (
          <div key={t.id} className={clsx('pointer-events-auto px-4 py-3 rounded-lg shadow-lg text-body-sm font-medium', typeStyles[t.type])}>
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
