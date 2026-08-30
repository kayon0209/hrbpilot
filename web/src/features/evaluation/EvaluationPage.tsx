import { useQuery } from '@tanstack/react-query'; import { getMetrics } from '../../api/evaluation'; import { AsyncState } from '../../components/AsyncState'; import styles from './EvaluationPage.module.css'; import { useSessionStore } from '../../app/session-store'; import { hasCapability } from '../../app/roles'; import { PermissionNotice } from '../../components/PermissionNotice'

const scenarioLabels: Record<string, string> = { policy_qa: '制度问答', interview_digest: '面谈纪要', voice_insight: '员工声音', weekly_report: 'HR 周报', culture_content: '文化内容' }
const metricLabels: Record<string, string> = { answer_relevance: '回答相关性', citation_accuracy: '引用准确率', retrieval_hit_rate: '检索命中率', retrieval_latency_ms: '检索延迟', latency_ms: '延迟', total_entries: '样本数' }

export function EvaluationPage(){const user=useSessionStore(state=>state.user);if(!hasCapability(user?.role,'evaluation'))return <main className="page-stack"><header className="page-heading"><div><h1>AI 质量</h1></div></header><PermissionNotice feature="AI 质量" requiredRole="管理员"/></main>;return <EvaluationMetrics/>}
function EvaluationMetrics(){const query=useQuery({queryKey:['evaluation'],queryFn:getMetrics});const raw=query.data?.scenarios;const scenarios=Array.isArray(raw)?raw:Object.entries(raw??{}).map(([scenario_id,value])=>({scenario_id,...value}));return <main className="page-stack"><header className="page-heading"><div><span className="eyebrow">管理后台</span><h1>AI 质量</h1><p>只显示后端已记录的评测数据；空数据不推导业务结论。指标用于判断系统质量，不代表员工体验。</p></div></header>{query.isPending&&<AsyncState kind="loading" title="正在读取评测指标"/>}{query.isError&&<AsyncState kind="error" title="评测数据不可用" detail={query.error.message}/>} {!query.isPending&&scenarios.length===0&&<AsyncState kind="empty" title="还没有评测记录" detail="运行评测后，结果会由后端聚合到这里。"/>}<div className={styles.grid}>{scenarios.map((item,index)=><MetricCard key={String(item.scenario_id??index)} data={item}/>)}</div><section className={styles.notice}><strong>可宣称边界</strong><p>引用准确率、正确拒答与延迟只能说明当前样本的表现，不能直接代表员工体验或管理成效。占位值会明确标记，不参与业务判断。</p></section></main>}

function formatMetric(key: string, val: unknown): string {
  if (typeof val !== 'number') return String(val ?? '—')
  if (key.includes('latency')) return `${Math.round(val)} ms`
  return val <= 1 ? `${Math.round(val * 100)}%` : String(Math.round(val * 100) / 100)
}

export function MetricCard({data,value,isStub}:{data?:Record<string,unknown>;value?:number;isStub?:boolean}){
  const record=data??{value}
  const stub=isStub||record.is_stub===true||record.stub===true
  const scenarioId=String(record.scenario_id??record.name??'指标')
  const metrics=record.metrics&&typeof record.metrics==='object'?record.metrics as Record<string,unknown>:null
  const metricEntries=metrics?Object.entries(metrics):[]
  const flat=Object.entries(record).filter(([key,val])=>!['scenario_id','name','is_stub','stub','metrics'].includes(key)&&typeof val!=='object')
  return <article className={styles.card}><div><span>{scenarioLabels[scenarioId]??scenarioId}</span>{stub&&<b>不可用于业务判断</b>}</div>
    {metricEntries.map(([key,val])=>{const stat=val&&typeof val==='object'?val as Record<string,unknown>:null;const primary=stat?(stat.latest??stat.avg):val;const count=stat&&typeof stat.count==='number'?stat.count:null;return <p key={key}><small>{metricLabels[key]??key.replaceAll('_',' ')}{count!==null&&` · ${count} 条样本`}</small><strong>{formatMetric(key,primary)}</strong></p>})}
    {flat.map(([key,val])=><p key={key}><small>{metricLabels[key]??key.replaceAll('_',' ')}</small><strong>{formatMetric(key,val)}</strong></p>)}
    {metricEntries.length===0&&flat.length===0&&<p><small>暂无指标数据</small></p>}
  </article>
}
