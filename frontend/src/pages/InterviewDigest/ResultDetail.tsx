/** HRBP AI Workbench — Result detail component for InterviewDigest. */

interface Demand { demand: string; category: string; urgency: string; }
interface ActionItem { action: string; owner: string; deadline: string; }
interface DigestResult {
  employee_demands: Demand[]; risk_level: string; risk_signals: string[];
  action_items: ActionItem[]; suggested_owner: string; summary: string;
  confidence: number; has_evidence: boolean;
}

const RISK_COLORS: Record<string, { bg: string; text: string; border: string; label: string }> = {
  HIGH: { bg: 'bg-danger-50', text: 'text-danger-600', border: 'border-danger-200', label: '高风险' },
  MEDIUM: { bg: 'bg-warning-50', text: 'text-warning-600', border: 'border-warning-200', label: '中风险' },
  LOW: { bg: 'bg-emerald-50', text: 'text-emerald-600', border: 'border-emerald-200', label: '低风险' },
};

const URGENCY_COLORS: Record<string, string> = {
  '高': 'bg-danger-50 text-danger-600 border-danger-200',
  '中': 'bg-warning-50 text-warning-600 border-warning-200',
  '低': 'bg-emerald-50 text-emerald-600 border-emerald-200',
};

interface ResultDetailProps {
  result: DigestResult;
}

export function ResultDetail({ result }: ResultDetailProps) {
  const riskStyle = RISK_COLORS[result.risk_level] || RISK_COLORS.LOW;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <div className={`badge border ${riskStyle.border} ${riskStyle.bg} ${riskStyle.text}`}>
          {riskStyle.label}
        </div>
        <div className="text-caption text-neutral-400">
          置信度: {(result.confidence * 100).toFixed(0)}%
        </div>
      </div>

      <div className="card">
        <h3 className="text-card-title text-neutral-600 mb-2">整体摘要</h3>
        <div className="text-body text-neutral-700">{result.summary}</div>
      </div>

      {result.employee_demands.length > 0 && (
        <div className="card">
          <h3 className="text-card-title text-neutral-600 mb-3">员工诉求</h3>
          <div className="space-y-2">
            {result.employee_demands.map((d, i) => (
              <div key={i} className="flex items-center gap-3 text-body-sm">
                <span className={`badge border ${URGENCY_COLORS[d.urgency] || URGENCY_COLORS['低']}`}>{d.urgency}</span>
                <span className="text-neutral-400">{d.category}</span>
                <span className="text-neutral-700">{d.demand}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {result.risk_signals.length > 0 && (
        <div className="card border-warning-200 bg-warning-50/30">
          <h3 className="text-card-title text-warning-600 mb-2">风险信号</h3>
          <div className="space-y-1">
            {result.risk_signals.map((s, i) => <div key={i} className="text-body-sm text-warning-700">· {s}</div>)}
          </div>
        </div>
      )}

      {result.action_items.length > 0 && (
        <div className="card">
          <h3 className="text-card-title text-neutral-600 mb-3">行动项</h3>
          <div className="space-y-2">
            {result.action_items.map((a, i) => (
              <div key={i} className="flex items-center gap-3 text-body-sm">
                <span className="text-primary-600 font-semibold">{i + 1}.</span>
                <span className="text-neutral-700">{a.action}</span>
                <span className="text-caption text-neutral-400">{a.owner} · {a.deadline}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {result.suggested_owner && (
        <div className="card bg-primary-50 border-primary-200">
          <div className="text-caption text-primary-600">建议跟进人: {result.suggested_owner}</div>
        </div>
      )}
    </div>
  );
}
