import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getMcpCapabilities, callMcpTool, type McpToolView } from '../../api/mcp'
import { AsyncState } from '../../components/AsyncState'

const EXAMPLES: Record<string, Record<string, unknown>> = {
  search_policy: { query: '请假超过三天需要哪些审批？', top_k: 3 },
  get_policy_source: { document_name: '请假管理制度.pdf' },
  hrbpilot_ping: {},
  create_hr_case: { case_id: '粘贴一个 HR 案件 ID', title: '试用期异常跟进', subject_ref: 'EMP-001', category: 'onboarding' },
  assign_case_owner: { case_id: '同上', owner_id: 'hr-manager-9' },
  send_case_notification: { case_id: '同上', recipient_ref: 'dept-hr', template: 'policy_update' },
  update_case_status: { case_id: '同上', status: 'RESOLVED' },
  create_work_task: { case_id: '同上', title: '完成入职材料补齐', next_action: '联系员工补充合同' },
}

function ToolCard({ tool, onTry }: { tool: McpToolView; onTry: (name: string) => void }) {
  const badge = tool.kind === 'write' ? '需审批' : '只读'
  return (
    <article style={{ border: '1px solid #e5e7eb', borderRadius: 12, padding: 14, background: '#fff' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <strong style={{ fontSize: 14 }}>{tool.name}</strong>
        <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 999, background: tool.kind === 'write' ? '#fef3c7' : '#e0f2fe' }}>{badge}</span>
      </div>
      <p style={{ fontSize: 12, color: '#6b7280', margin: '8px 0 0' }}>{tool.capability} · {tool.requires_approval ? '写前置审批' : '直接校验'}</p>
      <pre style={{ margin: '10px 0 0', padding: 10, background: '#f8fafc', borderRadius: 8, fontSize: 11, overflowX: 'auto' }}>
        {JSON.stringify(tool.input_schema, null, 2).slice(0, 900)}
      </pre>
      <button type="button" className="primary-button" style={{ marginTop: 10, padding: '6px 12px', fontSize: 13 }} onClick={() => onTry(tool.name)}>
        试调
      </button>
    </article>
  )
}

export function McpPage() {
  const caps = useQuery({ queryKey: ['mcp-capabilities'], queryFn: getMcpCapabilities })
  const [selected, setSelected] = useState<string>('search_policy')
  const [argsText, setArgsText] = useState<string>(JSON.stringify(EXAMPLES.search_policy, null, 2))
  const [result, setResult] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  function pick(name: string) {
    setSelected(name)
    setArgsText(JSON.stringify(EXAMPLES[name] ?? {}, null, 2))
    setResult(null)
    setError('')
  }

  async function run() {
    setBusy(true)
    setError('')
    setResult(null)
    try {
      const args = JSON.parse(argsText) as Record<string, unknown>
      const res = await callMcpTool(selected, args)
      setResult(res)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (caps.isPending) return <AsyncState kind="loading" title="正在读取 MCP 能力" />
  if (caps.isError) return <AsyncState kind="error" title="MCP 能力读取失败" detail={caps.error.message} />
  const data = caps.data!

  return (
    <main className="page-stack">
      <header className="page-heading">
        <div>
          <span className="eyebrow">外部工具</span>
          <h1>MCP 外部工具</h1>
          <p>复用同一套工具白名单与审批门禁：只读可直接试，写操作仅创建审批请求，不直执。</p>
        </div>
      </header>

      <section style={{ display: 'grid', gap: 12, gridTemplateColumns: '1fr', maxWidth: 980 }}>
        <div style={{ background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12, padding: 16 }}>
          <h2 style={{ fontSize: 14, margin: 0 }}>如何接入</h2>
          <div style={{ marginTop: 10, display: 'grid', gap: 10 }}>
            <div>
              <strong style={{ fontSize: 13 }}>本页试调（无需另配）：</strong>
              <p style={{ fontSize: 12, color: '#6b7280', margin: '4px 0 0' }}>登录后直接在下方选工具、改参数、点「执行」，结果走同一套白名单校验；写工具会返回 <code>approval_id</code>，去「团队待处理」批准后才进 Outbox。</p>
            </div>
            <div style={{ background: '#f8fafc', borderRadius: 8, padding: 12, fontSize: 12, lineHeight: 1.6 }}>
              <div><strong>stdio（本地/Inspector）：</strong> <code>python -m app.mcp.server</code> — 租户取自 MCP 请求头的 JWT，匿名读放行、匿名写拒。</div>
              <div style={{ marginTop: 6 }}><strong>HTTP：</strong> <code>POST /mcp</code>（Streamable HTTP）+ <code>/api/mcp/tools/&#123;name&#125;/call</code>（本页同源代理）— 前者需 <code>Authorization: Bearer &lt;JWT&gt;</code> + <code>Accept: application/json, text/event-stream</code>。</div>
              <div style={{ marginTop: 8, color: '#92400e' }}>租户：<code>{data.tenant_id}</code> · 作用域：<code>{data.scope}</code> · 写模式：<code>{data.write_mode}</code></div>
            </div>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <div>
            <h3 style={{ fontSize: 13, margin: '0 0 8px' }}>只读工具</h3>
            <div style={{ display: 'grid', gap: 10 }}>
              {data.read_tools.map(t => <ToolCard key={t.name} tool={t} onTry={pick} />)}
            </div>
          </div>
          <div>
            <h3 style={{ fontSize: 13, margin: '0 0 8px' }}>写工具（仅建审批）</h3>
            <div style={{ display: 'grid', gap: 10 }}>
              {data.write_tools.map(t => <ToolCard key={t.name} tool={t} onTry={pick} />)}
            </div>
          </div>
        </div>

        <div style={{ background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12, padding: 16 }}>
          <h2 style={{ fontSize: 14, margin: 0 }}>试调</h2>
          <div style={{ display: 'grid', gridTemplateColumns: '160px 1fr', gap: 12, marginTop: 12, alignItems: 'start' }}>
            <label style={{ display: 'grid', gap: 6, fontSize: 12 }}>
              工具
              <select value={selected} onChange={e => pick(e.target.value)} style={{ padding: '8px 10px', borderRadius: 8, border: '1px solid #d1d5db' }}>
                {[...data.read_tools, ...data.write_tools].map(t => <option key={t.name} value={t.name}>{t.name}</option>)}
              </select>
              <span style={{ color: '#6b7280' }}>写工具需在参数里填 <code>case_id</code></span>
            </label>
            <label style={{ display: 'grid', gap: 6, fontSize: 12 }}>
              参数 JSON
              <textarea value={argsText} onChange={e => setArgsText(e.target.value)} rows={8} style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12, padding: 10, borderRadius: 8, border: '1px solid #d1d5db' }} />
            </label>
          </div>
          <div style={{ marginTop: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
            <button type="button" className="primary-button" onClick={run} disabled={busy}>{busy ? '执行中…' : '执行'}</button>
            {error && <span style={{ color: '#b91c1c', fontSize: 12 }}>{error}</span>}
          </div>
          {result && (
            <pre style={{ marginTop: 12, padding: 12, background: '#0f172a', color: '#e2e8f0', borderRadius: 8, fontSize: 11, overflowX: 'auto', maxHeight: 420 }}>
              {JSON.stringify(result, null, 2)}
            </pre>
          )}
        </div>
      </section>
    </main>
  )
}
