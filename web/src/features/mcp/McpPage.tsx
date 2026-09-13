import { useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { callMcpTool, getMcpCapabilities, type McpCapabilities, type McpToolView } from '../../api/mcp'
import { AsyncState } from '../../components/AsyncState'
import { FALLBACK_TOOL_COPY, PARAM_HINTS, TOOL_COPY, describeResult } from './toolCopy'
import styles from './McpPage.module.css'

/** 在线体验的示例参数：每个工具给一份能直接跑通的内容。 */
const CASE_ID_PLACEHOLDER = '请替换成真实案件编号'

const EXAMPLES: Record<string, Record<string, unknown>> = {
  search_policy: { query: '请假超过三天需要哪些审批？', top_k: 3 },
  get_policy_source: { document_name: '请假管理制度.pdf' },
  hrbpilot_ping: {},
  create_hr_case: { case_id: CASE_ID_PLACEHOLDER, title: '试用期异常跟进', subject_ref: 'EMP-001', category: 'onboarding' },
  assign_case_owner: { case_id: CASE_ID_PLACEHOLDER, owner_id: 'hr-manager-9' },
  send_case_notification: { case_id: CASE_ID_PLACEHOLDER, recipient_ref: 'dept-hr', template: 'policy_update' },
  update_case_status: { case_id: CASE_ID_PLACEHOLDER, status: 'RESOLVED' },
  create_work_task: { case_id: CASE_ID_PLACEHOLDER, title: '完成入职材料补齐', next_action: '联系员工补充合同' },
}

function copyFor(name: string) {
  return TOOL_COPY[name] ?? FALLBACK_TOOL_COPY
}

function ToolCard({ tool, onTry }: { tool: McpToolView; onTry: (name: string) => void }) {
  const copy = copyFor(tool.name)
  const isWrite = tool.kind === 'write'
  return (
    <article className={styles.toolCard}>
      <header className={styles.toolHead}>
        <div className={styles.toolTitle}>
          <strong>{copy.label}</strong>
          <span className={styles.toolName}>{tool.name}</span>
        </div>
        <span className={isWrite ? styles.chipWrite : styles.chipRead}>{isWrite ? '办理类' : '查询类'}</span>
      </header>
      <p className={styles.toolWhat}>{copy.what}</p>
      <p className={styles.toolSay}>
        <span>可以这样对 AI 说</span>
        「{copy.say}」
      </p>
      {copy.note && <p className={styles.toolNote}>{copy.note}</p>}
      <button type="button" className={styles.tryButton} onClick={() => onTry(tool.name)}>
        在线体验
      </button>
    </article>
  )
}

function ConnectInfo({ data }: { data: McpCapabilities }) {
  const stdio = data.transports.find(t => t.kind === 'stdio')
  const remote = data.transports.find(t => t.kind !== 'stdio')
  return (
    <details className={styles.details}>
      <summary>查看给 IT / 管理员的接入信息</summary>
      <div className={styles.detailsBody}>
        <dl className={styles.meta}>
          <div>
            <dt>本地直连</dt>
            <dd>
              在服务器上运行 <code>{stdio?.command ?? 'python -m app.mcp.server'}</code>，适用于本机调试或桌面版 AI 助手直连。
            </dd>
          </div>
          <div>
            <dt>远程调用</dt>
            <dd>
              请求地址 <code>{remote?.url ?? '/mcp'}</code>（流式 HTTP）；本页的「在线体验」走的是同源通道，无需额外配置。
            </dd>
          </div>
          <div>
            <dt>身份与权限</dt>
            <dd>查询类允许匿名试用；办理类必须登录后携带身份凭证才受理。{data.auth}</dd>
          </div>
          <div>
            <dt>当前环境</dt>
            <dd>
              单位编号 <code>{data.tenant_id}</code> · 权限范围 <code>{data.scope}</code> · 办理模式 <code>{data.write_mode}</code>
            </dd>
          </div>
        </dl>
      </div>
    </details>
  )
}

export function McpPage() {
  const caps = useQuery({ queryKey: ['mcp-capabilities'], queryFn: getMcpCapabilities })
  const [selected, setSelected] = useState<string>('search_policy')
  const [argsText, setArgsText] = useState<string>(JSON.stringify(EXAMPLES.search_policy, null, 2))
  const [result, setResult] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const tryoutRef = useRef<HTMLElement>(null)

  function pick(name: string, scroll = false) {
    setSelected(name)
    setArgsText(JSON.stringify(EXAMPLES[name] ?? {}, null, 2))
    setResult(null)
    setError('')
    // 卡片上的「在线体验」把用户带到试调区；jsdom 没有 scrollIntoView，用可选调用兜底。
    if (scroll) tryoutRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
  }

  async function run() {
    setBusy(true)
    setError('')
    setResult(null)
    try {
      const args = JSON.parse(argsText) as Record<string, unknown>
      if (isWriteSelected) {
        const caseId = typeof args.case_id === 'string' ? args.case_id.trim() : ''
        if (!caseId || caseId === CASE_ID_PLACEHOLDER) {
          setError('办理类必须先有案件编号：请把参数里的 case_id 换成真实编号再提交。')
          return
        }
      }
      const res = await callMcpTool(selected, args)
      setResult(res)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (caps.isPending) return <AsyncState kind="loading" title="正在读取可用工具" />
  if (caps.isError) return <AsyncState kind="error" title="可用工具读取失败" detail={caps.error.message} />
  const data = caps.data!

  const allTools = [...data.read_tools, ...data.write_tools]
  const isWriteSelected = allTools.find(t => t.name === selected)?.kind === 'write'
  const selectedHints = PARAM_HINTS[selected] ?? {}
  const summary = result ? describeResult(result) : null

  return (
    <main className="page-stack">
      <header className="page-heading">
        <div>
          <span className="eyebrow">外部工具</span>
          <h1>MCP 外部工具</h1>
          <p className="lede">把你常用的 AI 助手接到这里，用一句大白话就能查制度、发起办理。</p>
        </div>
      </header>

      <section className={styles.introGrid}>
        <article className="panel">
          <h2>这是什么</h2>
          <p>
            MCP 是一种通用的「对接标准」。按这个标准接上之后，你就不用专门打开本系统了——直接在自己的 AI
            助手里提问，它会自动去调用对应的功能，办完再回来告诉你结果。
          </p>
          <p className={styles.muted}>
            你只管说话，AI 负责跑腿，本系统负责执行并留痕。
          </p>
          <p className={styles.noteBox}>
            注意：这里不是员工提交申请的入口。员工从企业微信 / 飞书提交的申请，请到「员工服务 → 员工请求」查看。
          </p>
        </article>
        <article className="panel">
          <h2>能帮你做什么</h2>
          <ul className={styles.capabilityList}>
            <li>
              <strong>查资料 · 随时可用</strong>
              <span>搜制度条款、调出制度原文，只看不改。</span>
            </li>
            <li>
              <strong>发起办理 · 需 HR 批准</strong>
              <span>新建案件、指派负责人、发通知、结案、加跟进任务。</span>
            </li>
          </ul>
          <p className={styles.callout}>
            办理类操作不会立刻生效：它会先变成一条「待审批」，由 HR 在「团队待处理」里确认后才真正执行。所以不必担心
            AI 改错数据。
          </p>
        </article>
      </section>

      <section>
        <h2 className={styles.sectionTitle}>两类工具怎么选</h2>
        <div className={styles.kindGrid}>
          <article className={styles.kindCard}>
            <header>
              <span className={styles.chipRead}>查询类</span>
              <strong>只看不改，放心试</strong>
            </header>
            <dl>
              <div>
                <dt>用途</dt>
                <dd>在制度资料里找条款、调出制度原文。</dd>
              </div>
              <div>
                <dt>什么时候用</dt>
                <dd>想快速了解一条制度；不知道该翻哪份文件；需要引用原文出处。</dd>
              </div>
              <div>
                <dt>注意事项</dt>
                <dd>只读，不会留下任何记录，也不会改动数据；参数填错最多是查不到结果。</dd>
              </div>
            </dl>
          </article>
          <article className={styles.kindCard}>
            <header>
              <span className={styles.chipWrite}>办理类</span>
              <strong>提交申请，HR 批准后才生效</strong>
            </header>
            <dl>
              <div>
                <dt>用途</dt>
                <dd>新建案件、指派负责人、发通知、把案件标记为已解决、新建跟进任务。</dd>
              </div>
              <div>
                <dt>什么时候用</dt>
                <dd>有明确的员工事务需要登记或推进时。</dd>
              </div>
              <div>
                <dt>注意事项</dt>
                <dd>必须先有「案件编号」；提交后只是「待审批」，不会立刻生效；重复提交会产生多条待审批记录。</dd>
              </div>
            </dl>
          </article>
        </div>
      </section>

      <section>
        <h2 className={styles.sectionTitle}>怎么用：三步</h2>
        <ol className={styles.steps}>
          <li>
            <span className={styles.stepNo}>第 1 步</span>
            <strong>接入（只需做一次）</strong>
            <p>把接入信息交给 IT 或管理员配置即可。如果你只是使用者，这步可以跳过。</p>
            <ConnectInfo data={data} />
          </li>
          <li>
            <span className={styles.stepNo}>第 2 步</span>
            <strong>直接开口问</strong>
            <p>接入之后，在 AI 助手里像跟同事说话一样提问，它会自动挑合适的工具去办。可以照抄下方每张卡片里的示例说法。</p>
          </li>
          <li>
            <span className={styles.stepNo}>第 3 步</span>
            <strong>确认办理</strong>
            <p>办理类操作会进入「团队待处理」，由 HR 批准后才真正执行，全程留有可追溯的审批记录。</p>
          </li>
        </ol>
      </section>

      <section>
        <h2 className={styles.sectionTitle}>可用工具一览（共 {allTools.length} 个）</h2>
        <div className={styles.catalog}>
          <div>
            <h3 className={styles.groupTitle}>查询类 · 只查不改</h3>
            <div className={styles.cardGrid}>
              {data.read_tools.map(t => (
                <ToolCard key={t.name} tool={t} onTry={n => pick(n, true)} />
              ))}
            </div>
          </div>
          <div>
            <h3 className={styles.groupTitle}>办理类 · 需 HR 批准</h3>
            <div className={styles.cardGrid}>
              {data.write_tools.map(t => (
                <ToolCard key={t.name} tool={t} onTry={n => pick(n, true)} />
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="panel" ref={tryoutRef}>
        <h2>在线体验（不装 AI 助手也能试）</h2>
        <p className={styles.muted}>
          选一个工具，把参数里的内容换成你自己的，点按钮就能看到真实返回结果——和你通过 AI 助手调用走的是同一套规则。
        </p>
        <div className={styles.tryLayout}>
          <div className={styles.trySide}>
            <label className={styles.field}>
              <span>选择工具</span>
              <select value={selected} onChange={e => pick(e.target.value)}>
                {allTools.map(t => (
                  <option key={t.name} value={t.name}>
                    {copyFor(t.name).label}（{t.name}）
                  </option>
                ))}
              </select>
            </label>
            <div className={styles.paramHints}>
              <strong>参数怎么填</strong>
              {Object.keys(selectedHints).length === 0 ? (
                <p className={styles.muted}>这个工具不需要填任何参数，直接点下方按钮即可。</p>
              ) : (
                <ul>
                  {Object.entries(selectedHints).map(([key, hint]) => (
                    <li key={key}>
                      <code>{key}</code>
                      <span>{hint}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
          <label className={styles.field}>
            <span>参数（格式不用记，只改引号里的内容就行）</span>
            <textarea
              value={argsText}
              onChange={e => setArgsText(e.target.value)}
              rows={10}
              spellCheck={false}
              aria-label="参数"
            />
          </label>
        </div>
        <div className={styles.tryActions}>
          <button type="button" className="primary-button" onClick={run} disabled={busy}>
            {busy ? '处理中…' : isWriteSelected ? '提交办理' : '开始查询'}
          </button>
          {error && <span className={styles.error}>{error}</span>}
        </div>
        {summary && result && (
          <div className={styles.result}>
            <p
              className={
                summary.tone === 'ok' ? styles.resultOk : summary.tone === 'warn' ? styles.resultWarn : styles.resultError
              }
            >
              {summary.text}
            </p>
            <details className={styles.details}>
              <summary>查看原始返回数据（技术同学用）</summary>
              <div className={styles.detailsBody}>
                <pre className={styles.raw}>{JSON.stringify(result, null, 2)}</pre>
              </div>
            </details>
          </div>
        )}
      </section>
    </main>
  )
}
