import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { getReadiness } from '../api/system'
import { AsyncState } from '../components/AsyncState'

const names: Record<string, string> = { database: '业务数据库', postgres: '业务数据库', redis: '任务队列', milvus: '向量检索服务', embedding: '向量模型', llm: '生成模型' }
export function OverviewPage() {
  const navigate = useNavigate()
  const ready = useQuery({ queryKey: ['readiness'], queryFn: getReadiness, refetchInterval: 30_000 }); const checks = Object.entries(ready.data?.checks ?? {}); const failed = checks.filter(([, v]) => v === false || v === 'error' || (typeof v === 'object' && v.status && v.status !== 'ok'))
  return <main className="page-stack"><header className="page-heading"><div><span className="eyebrow">TODAY / WORKBENCH</span><h1>概览</h1><p>先确认服务状态，再进入需要完成的工作。</p></div><button type="button" className="secondary-button" onClick={() => navigate('/knowledge')}>管理知识库</button></header>
    {ready.isPending && <AsyncState kind="loading" title="正在检查服务" detail="读取真实后端依赖状态。" />}{ready.isError && <AsyncState kind="error" title="无法读取服务状态" detail={ready.error.message} action={<button onClick={() => ready.refetch()}>重新检查</button>} />}
    {ready.data && <section className="readiness-card"><div><span className={`readiness-dot ${failed.length ? 'bad' : ''}`} /><div><h2>{failed.length ? '部分服务需要处理' : '核心服务运行正常'}</h2><p>{failed.length ? '受影响的功能已在下方列出。' : '可以开始知识检索与内容工作。'}</p></div></div><time>{new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })} 检查</time></section>}
    {failed.length > 0 && <section className="panel"><h2>需要处理</h2><div className="issue-list">{failed.map(([name, value]) => <article key={name}><strong>{names[name] ?? name}不可用</strong><p>{typeof value === 'object' ? value.detail ?? '连接检查未通过' : '连接检查未通过'}</p></article>)}</div></section>}
    <section className="workspace-grid"><Link to="/policy"><span>01</span><h2>制度问答</h2><p>从已索引制度中检索依据，并查看语义、关键词与综合排序证据。</p><b>开始查询 →</b></Link><Link to="/interview"><span>02</span><h2>面谈纪要</h2><p>将长文本转成结构化纪要、风险提示与后续行动。</p><b>整理面谈 →</b></Link><Link to="/weekly"><span>03</span><h2>内容工作</h2><p>生成 HR 周报或文化传播内容，并保留人工编辑环节。</p><b>创建材料 →</b></Link></section><p className="truth-note">本页不展示虚构 KPI。服务状态来自当前后端实时检查。</p>
  </main>
}
