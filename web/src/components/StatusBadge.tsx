export function StatusBadge({ status, label }: { status: string; label?: string }) {
  return <span className={`status-badge status-badge--${status}`}>{label ?? status}</span>
}
