import { Bubble, Bar } from 'react-chartjs-2';
import { Chart as ChartJS, Tooltip, Legend, PointElement, LinearScale, CategoryScale, BarElement } from 'chart.js';

ChartJS.register(Tooltip, Legend, PointElement, LinearScale, CategoryScale, BarElement);

// === Types ===

interface ClusterData { label: string; demand_count: number; demands: string[]; severity: string; }
interface TrendData { topic: string; direction: string; confidence: number; evidence: string; }

// === Cluster Bubble Chart ===

const SEVERITY_RADIUS: Record<string, number> = { HIGH: 16, MEDIUM: 12, LOW: 8 };
const SEVERITY_COLOR: Record<string, string> = { HIGH: 'rgba(239,68,68,0.7)', MEDIUM: 'rgba(245,158,11,0.7)', LOW: 'rgba(16,185,129,0.7)' };

export function ClusterBubbleChart({ clusters }: { clusters: ClusterData[] }) {
  const data = {
    datasets: clusters.map((c, i) => ({
      label: c.label,
      data: [{ x: i + 1, y: c.demand_count, r: SEVERITY_RADIUS[c.severity] || 8 }],
      backgroundColor: SEVERITY_COLOR[c.severity] || 'rgba(99,102,241,0.6)',
    })),
  };
  const options = {
    responsive: true,
    plugins: { legend: { display: true, position: 'bottom' as const } },
    scales: { x: { title: { display: true, text: '主题' } }, y: { title: { display: true, text: '诉求数量' } } },
  };
  return <Bubble data={data} options={options} />;
}

// === Trend Bar Chart ===

export function TrendBarChart({ trends }: { trends: TrendData[] }) {
  const data = {
    labels: trends.map((t) => t.topic),
    datasets: [{
      label: '趋势置信度',
      data: trends.map((t) => t.direction === '上升' ? t.confidence * 100 : -t.confidence * 100),
      backgroundColor: trends.map((t) => t.direction === '上升' ? 'rgba(16,185,129,0.7)' : 'rgba(239,68,68,0.7)'),
    }],
  };
  const options = {
    responsive: true,
    indexAxis: 'y' as const,
    plugins: { legend: { display: false } },
    scales: { x: { title: { display: true, text: '变化幅度 (%)' } } },
  };
  return <Bar data={data} options={options} />;
}
