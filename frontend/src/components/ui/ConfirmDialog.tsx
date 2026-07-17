import { useEffect, useRef } from 'react';

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open, title, message, confirmLabel = '确定', cancelLabel = '取消',
  danger = false, onConfirm, onCancel,
}: ConfirmDialogProps) {
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (open) {
      confirmRef.current?.focus();
      const handler = (e: KeyboardEvent) => {
        if (e.key === 'Escape') onCancel();
      };
      document.addEventListener('keydown', handler);
      return () => document.removeEventListener('keydown', handler);
    }
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" onClick={onCancel}>
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" />
      <div
        className="relative bg-white rounded-xl shadow-2xl max-w-sm w-full mx-4 overflow-hidden animate-in"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-6 pt-6 pb-2">
          <h3 className="text-lg font-semibold text-neutral-800">{title}</h3>
          <p className="text-sm text-neutral-500 mt-2 leading-relaxed">{message}</p>
        </div>
        <div className="px-6 py-4 flex justify-end gap-3 border-t border-neutral-100 mt-2">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm text-neutral-600 hover:bg-neutral-50 rounded-lg transition-colors"
          >
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            onClick={onConfirm}
            style={{
              padding: '8px 16px',
              fontSize: '14px',
              fontWeight: 500,
              color: '#fff',
              background: danger ? '#DC2626' : '#E85D04',
              border: 'none',
              borderRadius: '8px',
              cursor: 'pointer',
              transition: 'background 150ms',
            }}
            onMouseEnter={(e) => { (e.target as HTMLButtonElement).style.background = danger ? '#B91C1C' : '#C94A03'; }}
            onMouseLeave={(e) => { (e.target as HTMLButtonElement).style.background = danger ? '#DC2626' : '#E85D04'; }}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
