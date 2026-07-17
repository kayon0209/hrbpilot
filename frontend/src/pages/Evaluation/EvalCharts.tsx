import { Bar, Line } from 'react-chartjs-2';
import { Chart as ChartJS, CategoryScale, LinearScale, BarElement, PointElement, LineElement, Tooltip, Legend } from 'chart.js';

ChartJS.register(CategoryScale, LinearScale, BarElement, PointElement, LineElement, Tooltip, Legend);

// === Types ===

interface ScenarioMetric { scenario: string; accuracy: number; latency: number; guardrail_pass: number; human_feedback: number; }
interface TrendPoint { timestamp: string; score: number; }

// === Scenario Comparison Bar Chart ===

const SCENARIO_COLORS = ['rgba(99,102,241,0.7)', 'rgba(14,165,233,0.7)', 'rgba(16,185,129,0.7)', 'rgba(245,158,11,0.7)', 'rgba(244,63,94,0.7)'];

export function ScenarioBarChart({ metrics }: { metrics: ScenarioMetric[] }) {
  const data = {
    labels: metrics.map((m) => m.scenario),
    datasets: [
      { label: '准确率', data: metrics.map((m) => m.accuracy * 100), backgroundColor: SCENARIO_COLORS },
      { label: '护栏通过率', data: metrics.map((m) => m.guardrail_pass * 100), backgroundColor: 'rgba(148,163,184,0.5)' },
    ],
  };
  const options = {
    responsive: true,
    plugins: { legend: { position: 'bottom' as const } },
    scales: { y: { beginAtZero: true, max: 100, title: { display: true, text: '%' } } },
  };
  return <Bar data={data} options={options} />;
}

// === Trend Line Chart ===

const LINE_COLORS = ['rgb(99,102,241)', 'rgb(14,165,233)', 'rgb(16,185,129)', 'rgb(245,158,11)', 'rgb(244,63,94)'];

export function TrendLineChart({ data }: { data: Record<string, TrendPoint[]> }) {
  const scenarios = Object.keys(data);
  if (scenarios.length === 0) {
    return <div className="text-caption text-neutral-400 text-center py-10">暂无趋势数据</div>;
  }

  const allTimestamps = [...new Set(
    scenarios.flatMap((s) => (data[s] || []).map((p) => p.timestamp))
  )].sort();

  const chartData = {
    labels: allTimestamps.map((t) => t.slice(5)),
    datasets: scenarios.map((s, i) => ({
      label: s,
      data: allTimestamps.map((ts) => {
        const point = (data[s] || []).find((p) => p.timestamp === ts);
        return point ? point.score : null;
      }),
      borderColor: LINE_COLORS[i % LINE_COLORS.length],
      backgroundColor: LINE_COLORS[i % LINE_COLORS.length] + '33',
      tension: 0.3,
      spanGaps: true,
    })),
  };
  const options = {
    responsive: true,
    plugins: { legend: { position: 'bottom' as const } },
    scales: { y: { min: 0.5, max: 1.0, title: { display: true, text: '准确率' } } },
  };
  return <Line data={chartData} options={options} />;
}
