export function ResultDocument({ result }: { result: Record<string, unknown> }) {
  // 只渲染有中文映射的 section —— 未映射的后端字段（has_evidence、task_id 等）不暴露给用户
  const visible = Object.entries(result).filter(
    ([key, value]) => value != null && value !== '' && labels[key] !== undefined,
  )
  return <div className="result-document">{visible.map(([key, value]) => <section key={key}><h3>{labels[key]}</h3>{renderValue(value)}</section>)}</div>
}

function renderValue(value: unknown) {
  if (Array.isArray(value)) {
    return <div className="result-list">{value.length ? value.map((item, index) => <article key={index}>{renderEntry(item)}</article>) : <p>暂无内容</p>}</div>
  }
  if (typeof value === 'object' && value) {
    return <dl>{Object.entries(value as Record<string, unknown>).filter(([k]) => itemLabels[k] !== undefined).map(([k, v]) => <div key={k}><dt>{itemLabels[k]}</dt><dd>{formatValue(v)}</dd></div>)}</dl>
  }
  return <p>{formatValue(value)}</p>
}

function renderEntry(item: unknown) {
  if (typeof item !== 'object' || !item) return String(item)
  return Object.entries(item as Record<string, unknown>).filter(([k]) => itemLabels[k] !== undefined).map(([k, v]) => <p key={k}><strong>{itemLabels[k]}</strong>{formatValue(v)}</p>)
}

function formatValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(String).join('、')
  return String(value)
}

const labels: Record<string, string> = {
  // 顶层 section（对齐后端四个场景的 Response schema）
  summary: '摘要', key_points: '关键事项', action_items: '后续行动', risks: '风险提示',
  sentiment: '情绪判断', themes: '主题', recommendations: '建议', progress: '本周进展',
  plan: '下周计划', data_sources: '数据来源', news_article: '新闻稿',
  group_notice: '群通知', employee_story: '员工故事', event_copy: '活动文案',
  keywords_used: '使用关键词', tone: '内容基调', period: '报告周期',
  employee_demands: '员工诉求', risk_level: '风险等级', risk_signals: '风险信号',
  suggested_owner: '建议跟进人', clusters: '主题聚类', trends: '趋势观察',
}
const itemLabels: Record<string, string> = {
  // 列表条目内字段（对齐后端 Demand/ActionItem/Cluster/RiskSignal/Trend/ProgressItem/RiskItem/PlanItem）
  demand: '诉求', category: '分类', urgency: '紧迫程度',
  item: '事项', source: '来源', status: '状态',
  risk: '风险', severity: '级别', owner: '负责人', action: '行动',
  task: '任务', priority: '优先级', deadline: '截止时间',
  label: '主题', demand_count: '诉求数量', demands: '典型诉求',
  signal: '信号', trend: '趋势', topic: '主题', direction: '方向',
  evidence: '依据',
}
