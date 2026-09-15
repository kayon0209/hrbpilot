/** AI 助手接入 —— 面向 HR 本人的自助接入页。
 *
 * 改版要点（2026-09-15）
 * ---------------------
 * 用户的 AI 助手装在他自己的电脑上，接入配置是本人做的，不存在"交给 IT 统一部署"
 * 这一步。所以这页的主行动是「复制这条命令」，而不是「把配置转发给别人」。据此：
 *
 * 1. **首屏只回答一个问题** —— 我下一步做什么。按接入状态二选一，其余全部下沉。
 * 2. **按角色下沉** —— 日常用法给 HR；联调工具给开发（默认收起）；租户级管理仅
 *    管理员可见。三种人不再共用一条阅读路径。
 * 3. **删掉三段元叙述** —— "三句话看懂这页""两种没反应是正常的""查询和办理有什么
 *    不一样"。用一段话解释页面，说明结构本身不自明；而"没反应是正常的"写在主流程
 *    上，等于开屏告诉用户这东西经常不好使。
 *
 * 接入地址用 `window.location.origin` 拼：nginx 已把 `/mcp/` 与 `/api/` 反代到
 * 应用，前端同源，所以用户不用手改、也不用知道部署在哪。
 */

import { useRef, useState, type ChangeEvent } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  callMcpTool,
  enableMcpClient,
  getMcpCapabilities,
  getMyConnections,
  listMcpClients,
  listMcpInstallations,
  revokeAllMcpInstallations,
  revokeMcpClient,
  revokeMcpInstallation,
  revokeMyInstallation,
  type McpCapabilities,
  type McpInstallation,
  type ConnectionCall,
  type McpToolResult,
  type McpToolView,
} from '../../api/mcp'
import { AsyncState } from '../../components/AsyncState'
import { FALLBACK_TOOL_COPY, FIELD_HINTS, TOOL_COPY, describeResult } from './toolCopy'
import {
  applyFieldValue,
  buildForm,
  firstMissingRequired,
  initialArgs,
  parseArgs,
  valuesFromArgs,
  type FormField,
} from './toolForm'
import styles from './McpPage.module.css'

/** 在线体验的示例参数：每个工具给一份能直接跑通的内容。 */
const CASE_ID_PLACEHOLDER = '请替换成真实案件编号'

const EXAMPLES: Record<string, Record<string, unknown>> = {
  search_policy: { query: '请假超过三天需要哪些审批？', top_k: 3 },
  get_policy_source: { document_name: '请假管理制度.pdf' },
  create_hr_case: { case_id: CASE_ID_PLACEHOLDER, title: '试用期异常跟进', subject_ref: 'EMP-001', category: 'onboarding' },
  assign_case_owner: { case_id: CASE_ID_PLACEHOLDER, owner_id: 'hr-manager-9' },
  send_case_notification: { case_id: CASE_ID_PLACEHOLDER, recipient_ref: 'dept-hr', template: 'policy_update' },
  update_case_status: { case_id: CASE_ID_PLACEHOLDER, status: 'RESOLVED' },
  create_work_task: { case_id: CASE_ID_PLACEHOLDER, title: '完成入职材料补齐', next_action: '联系员工补充合同' },
}

const INITIAL_TOOL = 'search_policy'

function copyFor(name: string) {
  return TOOL_COPY[name] ?? FALLBACK_TOOL_COPY
}

/* ---------------------------------------------------------------------- */
/* 助手与接入步骤                                                          */
/* ---------------------------------------------------------------------- */

type AssistantId = 'codex' | 'workbuddy' | 'claude' | 'other'

/** 兼容性状态。**这是事实标注，不是承诺** —— 不能出现"已验证"直到真的验证过。
 *
 *  当前全部是 `config`：我们提供了可用的配置方式，但本环境里**没有任何一个客户端
 *  完成过端到端授权**（`oauth_clients` 表为空），所以既不能标"已验证"，也不该让
 *  用户以为点一下就能连上。等某个助手真的走通了完整链路（注册 → 授权 → 调用成功），
 *  才把它的 `status` 改成 `verified`。
 */
type AssistantStatus = 'verified' | 'beta' | 'config' | 'planned'

const ASSISTANT_STATUS_LABEL: Record<AssistantStatus, string> = {
  verified: '已验证',
  beta: 'Beta',
  config: '配置支持',
  planned: '即将支持',
}

const ASSISTANTS: Array<{ id: AssistantId; label: string; mode: string; status: AssistantStatus }> = [
  { id: 'codex', label: 'Codex', mode: '终端命令', status: 'config' },
  { id: 'claude', label: 'Claude Code', mode: '终端命令', status: 'config' },
  { id: 'workbuddy', label: 'WorkBuddy', mode: '图形界面', status: 'config' },
  { id: 'other', label: '其它助手', mode: '通用配置', status: 'config' },
]

/** 该助手的接入步骤。命令里的地址与权限范围都由调用方拼好传进来。 */
function installSteps(id: AssistantId, url: string, scopes: string) {
  if (id === 'codex') {
    return {
      intro: '打开终端（Windows 用 PowerShell），把两条命令依次粘进去运行。',
      guide: [] as string[],
      commands: [
        { text: `codex mcp add hrbpilot --url ${url}`, hint: '这条告诉你的 Codex：HRBPilot 在哪。' },
        {
          text: `codex mcp login hrbpilot --scopes ${scopes}`,
          hint: '这条会打开浏览器让你登录并授权；登录后回到终端就完成了。若你的 Codex 版本对 --scopes 写法报错，先去掉该参数重试。',
        },
      ],
    }
  }
  if (id === 'claude') {
    return {
      intro: '在终端里运行下面这条，Claude Code 会自动带你完成授权。',
      guide: [] as string[],
      commands: [
        {
          text: `claude mcp add --transport http hrbpilot ${url}`,
          hint: '第一次在这里调用工具时，浏览器会自动弹出登录页 —— 登录一次即可。',
        },
      ],
    }
  }
  if (id === 'workbuddy') {
    return {
      intro: '不用命令行，在 WorkBuddy 里点几下就行。',
      guide: [
        '打开 WorkBuddy → 设置 → 连接器',
        '点「添加」，名称填 HRBPilot',
        '地址填下面这条（点右边的复制）',
        '点「授权」，浏览器会弹出登录页，登录即完成',
      ],
      commands: [{ text: url, hint: '' }],
    }
  }
  return {
    intro: '只要你的助手支持「远程 MCP 服务器」，填下面这条地址、用浏览器授权即可。',
    guide: ['授权方式选「OAuth」'],
    commands: [
      {
        text: url,
        hint: '如果你的助手只支持填密钥，那暂时接不上 —— 请改用上面列出的助手。',
      },
    ],
  }
}

/** 步骤状态点。数字本身不表达进度，用户看不出"我走到哪了" —— 用 ✓/●/○ 标出来。 */
function StepBadge({ no, state, label }: { no: number; state: 'done' | 'active' | 'todo'; label: string }) {
  const mark = state === 'done' ? '✓' : state === 'active' ? '●' : '○'
  const stateText = state === 'done' ? '已完成' : state === 'active' ? '进行中' : '尚未开始'
  return (
    <span className={styles.stepNo} data-state={state} title={`第 ${no} 步（${stateText}）：${label}`}>
      <span aria-hidden="true">{mark}</span>
      {no}
      <span className={styles.srOnly}>{stateText}</span>
    </span>
  )
}

/** 三步自助接入。未接入时铺在首屏，已接入时收进「再接一个」折叠 —— 同一份实现，
 *  两个位置不会各自演化出不同的命令或漏掉某一步。 */
function InstallSteps({
  data,
  assistant,
  onPickAssistant,
  plan,
  onPickPlan,
  hasInstallation,
  planTouched,
  copiedOnce,
  onCopied,
}: {
  data: McpCapabilities
  assistant: AssistantId
  onPickAssistant: (id: AssistantId) => void
  plan: string
  onPickPlan: (key: string) => void
  hasInstallation: boolean
  planTouched: boolean
  copiedOnce: boolean
  onCopied: () => void
}) {
  // 接入地址按**当前访问地址**拼：nginx 已把 /mcp/ 与 /api/ 反代到应用，前端同源，
  // 所以用户不用手改地址、也不用知道部署在哪（也因此不会有写死的 localhost）。
  const origin = typeof window === 'undefined' ? '' : window.location.origin
  const endpoint = `${origin}/mcp/tasks`
  // 权限套餐从后端 manifest 派生 —— 不在这里硬编码 scope 串，那会是第二份事实源，
  // 而改了后端没改前端会让用户照着界面配出一个权限不对的助手。
  const packages = Object.entries(data.scope_packages ?? {})
  const activePlan = plan || packages[0]?.[0] || ''
  const scopes = (packages.find(([key]) => key === activePlan)?.[1].scopes ?? []).join(',')
  const steps = installSteps(assistant, endpoint, scopes)
  const picked = ASSISTANTS.find(item => item.id === assistant)

  // 「复制全部步骤」：整段流程拼成一段可粘贴文本。逐条复制时最容易漏掉第二条命令，
  // 而只跑第一条是接不上的 —— 漏了那一步的人会以为"照做了但没反应"。
  const allStepsText = [
    steps.intro,
    ...steps.guide.map((line, index) => `${index + 1}. ${line}`),
    ...steps.commands.map(command => command.text),
  ].join('\n')

  return (
    <>
      <div className={styles.stepBlock}>
        <div className={styles.stepHead}>
          <StepBadge no={1} state={planTouched ? 'done' : 'active'} label="选择权限范围" />
          <span className={styles.stepTitle}>先选权限范围</span>
        </div>
        <p className={styles.stepWhy}>
          决定这个助手能替你做多少事。以后想改，重新走一遍第 2 步就行 —— 旧的连接会自动失效，不会两条同时在用。
        </p>
        <div className={styles.plans}>
          {packages.map(([key, pkg]) => (
            <label key={key} className={styles.plan}>
              <input
                type="radio"
                name="scope-plan"
                value={key}
                checked={key === activePlan}
                onChange={() => onPickPlan(key)}
              />
              <span>
                <span className={styles.planTitle}>{pkg.label ?? key}</span>
                {/* 说明文案来自后端 `scope_packages[].description`，不按 key 名猜：
                    猜的写法一旦键名改了就会静默配错说明（写着"只查不改"却给了写权限）。 */}
                {pkg.description && <span className={styles.planDetail}>{pkg.description}</span>}
              </span>
            </label>
          ))}
        </div>

        {/* 数据范围：只让用户选"仅查询 / 能办理"还不够 —— 他还想知道**助手能看到什么**。
            下面这段照后端真实判定写（案件可见性过滤、租户隔离、写工具只建审批），
            不是宣传语；系统里没有的能力不写进来，那会变成无法兑现的承诺。 */}
        <div className={styles.dataScope}>
          <div>
            <h4 className={styles.dataScopeTitle}>允许访问</h4>
            <ul className={styles.dataScopeList}>
              <li>制度原文与出处</li>
              <li>你创建或负责的案件</li>
              <li>这些案件的审批状态</li>
              <li>你自己的权限摘要</li>
            </ul>
          </div>
          <div>
            <h4 className={styles.dataScopeTitle}>不会发生</h4>
            <ul className={styles.dataScopeList}>
              <li>读不到其它组织的任何数据</li>
              <li>读不到不属于你的案件</li>
              <li>不会直接修改案件、任务或通知</li>
              <li>助手不能批准申请：批准只在 HRBPilot 内由审批人完成</li>
            </ul>
          </div>
        </div>
        <p className={styles.dataScopeNote}>本系统不保存员工个人档案，助手也就无从读到它。</p>
      </div>

      <div className={styles.stepBlock}>
        <div className={styles.stepHead}>
          <StepBadge no={2} state={hasInstallation ? 'done' : copiedOnce ? 'active' : 'todo'} label="连接助手" />
          <span className={styles.stepTitle}>按下面的步骤接上</span>
        </div>
        <div className={styles.assistantPicks} role="group" aria-label="选择你的 AI 助手">
          {ASSISTANTS.map(item => (
            <button
              key={item.id}
              type="button"
              className={styles.assistantPick}
              aria-pressed={item.id === assistant}
              onClick={() => onPickAssistant(item.id)}
            >
              <span className={styles.assistantName}>{item.label}</span>
              {/* 兼容性状态照实标 —— 没走通过端到端验证就不写"已验证"，否则是替
                  尚未验证的兼容性提前承诺。状态值见 ASSISTANTS 的定义。 */}
              <span className={styles.assistantMeta}>
                {item.mode} · {ASSISTANT_STATUS_LABEL[item.status]}
              </span>
            </button>
          ))}
        </div>
        <p className={styles.stepWhy}>{steps.intro}</p>
        {steps.guide.length > 0 && (
          <ol className={styles.guideList}>
            {steps.guide.map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ol>
        )}
        <div className={styles.cmdList}>
          {steps.commands.map(command => (
            <div key={command.text}>
              <div className={styles.cmdRow}>
                <code className={styles.cmdCode}>{command.text}</code>
                <CopyButton text={command.text} copiedLabel="命令已复制" onCopied={onCopied} />
              </div>
              {command.hint && <p className={styles.cmdHint}>{command.hint}</p>}
            </div>
          ))}
        </div>
        <div className={styles.stepActions}>
          <CopyButton text={allStepsText} label="复制全部步骤" copiedLabel="全部步骤已复制" onCopied={onCopied} />
          <span className={styles.muted}>
            复制的是 {picked?.label} 的完整步骤；上面的地址按你当前的访问地址自动生成（正式环境会是
            https 域名），不用手改。
          </span>
        </div>
        <p className={styles.cmdHint}>
          终端里用右键或 Ctrl+Shift+V 粘贴；命令看起来折成两行是终端换行，内容没有变。
        </p>
      </div>

      <div className={styles.stepBlock}>
        <div className={styles.stepHead}>
          <StepBadge no={3} state={hasInstallation ? 'active' : 'todo'} label="确认连通" />
          <span className={styles.stepTitle}>试一句，确认通了</span>
        </div>
        <p className={styles.stepWhy}>
          在你刚接好的助手里问这句话。能给出制度出处，就说明通了 —— 这是唯一能证明"助手真的连上了"的办法：
          网页这边看到的地址与权限对不对，与助手能不能调通是两件事。
        </p>
        <div className={styles.cmdList}>
          <div className={styles.cmdRow}>
            <code className={styles.cmdCode}>帮我查一下试用期最长能多久</code>
            <CopyButton text="帮我查一下试用期最长能多久" copiedLabel="测试问题已复制" />
          </div>
        </div>
      </div>
    </>
  )
}

/** 把 client_id 归成人看得懂的名字。认不出来就保持中性的「AI 助手」，不显示原始 id。 */
function assistantName(installation: McpInstallation) {
  const key = (installation.client_id || '').toLowerCase()
  if (key.includes('codex')) return 'Codex'
  if (key.includes('workbuddy')) return 'WorkBuddy'
  if (key.includes('claude')) return 'Claude Code'
  return 'AI 助手'
}

/** 已接入实例的权限范围，一句话说清"它能不能发起办理"。
 *
 *  判据是**全权写入位**（`hrb:case:propose`）在不在 scope 里，而不是数 scope 个数。
 *  措辞与后端 `SCOPE_PACKAGES.query_and_propose.label` 保持一致：两处都面向用户，
 *  说法不同会让人以为它们指的不是同一件事（那里是"套餐名"，这里是"这个实例的能力"）。
 */
function scopeSummary(installation: McpInstallation) {
  return (installation.scopes ?? []).includes('hrb:case:propose') ? '查询 + 发起办理申请' : '仅查询'
}

/** 相对时间。连接管理中心要说"最近使用 3 分钟前"，而不是让用户自己换算时差。 */
function relativeTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const minutes = Math.floor((Date.now() - new Date(iso).getTime()) / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  return `${Math.floor(hours / 24)} 天前`
}

/** 复制按钮。
 *
 *  成功与失败必须分开说：剪贴板在非安全上下文（http 且非 localhost）不存在或会被
 *  拒绝，此时回落到"请手动选中"，**不能假装成功** —— 用户粘出来是旧内容时会把错
 *  命令送进终端，而且完全不知道原因（而这正是整条流程最关键的一次复制）。
 *
 *  `copiedLabel` 让每条命令各自回报（"连接命令已复制"），而不是所有按钮都说"已复制"：
 *  屏幕阅读器与视觉用户都能确认**是哪一条**被复制了。`aria-live` 让读屏念出变化。
 */
function CopyButton({
  text,
  label = '复制',
  copiedLabel = '已复制',
  onCopied,
}: {
  text: string
  label?: string
  copiedLabel?: string
  onCopied?: () => void
}) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  return (
    <button
      type="button"
      className={styles.cmdCopy}
      data-state={state}
      aria-live="polite"
      onClick={async () => {
        try {
          if (!navigator.clipboard) throw new Error('clipboard unavailable')
          await navigator.clipboard.writeText(text)
          setState('copied')
          onCopied?.()
        } catch {
          setState('failed')
        }
        window.setTimeout(() => setState('idle'), 2500)
      }}
    >
      {state === 'copied' ? copiedLabel : state === 'failed' ? '请手动选中复制' : label}
    </button>
  )
}

/* ---------------------------------------------------------------------- */
/* 表单控件与工具卡（在线体验用）                                          */
/* ---------------------------------------------------------------------- */

function FieldControl({
  field,
  value,
  onChange,
}: {
  field: FormField
  value: string
  onChange: (next: string) => void
}) {
  const common = {
    value,
    'aria-label': field.label,
    // 必填不只画一个「必填」小标签：连同 aria-required 一起给，屏幕阅读器与
    // 测试都能据此判断，不用去比对样式类名。
    'aria-required': field.required || undefined,
    onChange: (e: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => onChange(e.target.value),
  }
  return (
    <label className={styles.formField}>
      <span className={styles.formLabel}>
        {field.label}
        {field.required && <em className={styles.required}>必填</em>}
      </span>
      {field.kind === 'select' ? (
        <select {...common}>
          <option value="">不填（用系统默认）</option>
          {field.options?.map(option => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      ) : field.kind === 'datetime' ? (
        <input type="datetime-local" {...common} />
      ) : field.kind === 'number' ? (
        <input type="number" {...common} />
      ) : (
        <input type="text" placeholder={FIELD_HINTS[field.key]} {...common} />
      )}
    </label>
  )
}

function ToolCard({ tool, onTry }: { tool: McpToolView; onTry: (name: string) => void }) {
  const isWrite = tool.kind === 'write'
  const copy = copyFor(tool.name)
  return (
    <article className={styles.toolCard}>
      <header className={styles.toolHead}>
        <div className={styles.toolTitle}>
          <strong>{copy.label}</strong>
        </div>
        <span className={isWrite ? styles.chipWrite : styles.chipRead}>{isWrite ? '办理类' : '查询类'}</span>
      </header>
      <p className={styles.toolWhat}>{copy.what}</p>
      {copy.note && <p className={styles.toolNote}>{copy.note}</p>}
      <p className={styles.toolSay}>
        <span>可以这样对 AI 说</span>「{copy.say}」
      </p>
      <button type="button" className={styles.tryButton} onClick={() => onTry(tool.name)}>
        在线体验
      </button>
    </article>
  )
}

/* ---------------------------------------------------------------------- */
/* 我接过的助手（用户自助，可解绑）                                        */
/* ---------------------------------------------------------------------- */

/** 单个连接的运行统计（来自调用审计）。身份/权限字段走 capabilities，运行统计走这里 ——
 *  两个来源、两种变化频率，混在一起会让"实例列表"和"最近调用"绑死在同一份缓存上。 */
interface CallStats {
  last_call: ConnectionCall | null
  last_failure: (ConnectionCall & { deny_reason: string | null }) | null
  calls_7d: number
}

/** 「我接过的助手」。**只管列表**：空状态与"读不到"由调用方按上下文讲。
 *
 *  这里只显示**后端确实有的字段**：状态、权限范围、连接时间、近七天用量、最近使用、
 *  最近失败。"当前设备""暂停"需要新的后端字段，没有数据就不摆空壳 ——
 *  一个永远显示"未知"的字段比不显示更糟。
 */
function MyAssistants({
  installations,
  callStats,
  onRevoked,
}: {
  installations: McpInstallation[]
  callStats: Record<string, CallStats>
  onRevoked: () => void
}) {
  const revoke = useMutation({
    mutationFn: (familyId: string) => revokeMyInstallation(familyId),
    onSuccess: onRevoked,
  })

  return (
    <>
      {revoke.isError && <p className={styles.error}>断开失败：{revoke.error.message}</p>}
      <div className={styles.mineList}>
        {installations.map(item => {
          const name = assistantName(item)
          // 还活着 = 有未撤销的凭据且整条链没被撤销。
          const healthy = item.active_refresh_tokens > 0 && !item.revoked_at
          const stats = callStats[item.family_id]
          return (
            <div key={item.family_id} className={styles.mineRow}>
              <div className={styles.mineWho}>
                <span className={styles.mineName}>
                  {name}
                  <span className={healthy ? styles.mineStateOk : styles.mineStateOff}>
                    {healthy ? '正常' : '已失效'}
                  </span>
                </span>
                <span className={styles.mineMeta}>
                  {scopeSummary(item)}
                  {stats
                    ? ` · 近 7 天调用 ${stats.calls_7d} 次${
                        stats.last_call ? ` · 最近使用 ${relativeTime(stats.last_call.at)}` : ' · 尚无调用记录'
                      }`
                    : ''}
                  {item.created_at ? ` · 连接于 ${new Date(item.created_at).toLocaleDateString()}` : ''}
                </span>
                {stats?.last_failure && (
                  // 只报"还没有被后续成功覆盖"的失败 —— 已经恢复的故障继续挂着，
                  // 用户会去排查一个其实已经好了的问题。
                  <span className={styles.mineAlert}>
                    最近一次失败：{stats.last_failure.outcome}（{relativeTime(stats.last_failure.at)}）
                  </span>
                )}
              </div>
              <button
                type="button"
                className={styles.dangerButton}
                disabled={revoke.isPending}
                onClick={() => {
                  // 断开不是"关掉一条连接"这么轻：后端 `_cancel_bound_approvals` 会把这个
                  // 助手提交过、还在等审批或**已批准待执行**的办理一并置为过期。
                  // 记录不会消失，但那些办理需要重新提交 —— 这一点必须说在按下之前。
                  const ok = window.confirm(
                    `断开 ${name}？\n\n` +
                      `断开后，${name} 将立即无法继续访问 HRBPilot。\n` +
                      '它提交过、还在等审批或已批准待执行的办理会一并作废；记录会保留，但需要重新提交。',
                  )
                  if (ok) revoke.mutate(item.family_id)
                }}
              >
                断开连接
              </button>
            </div>
          )
        })}
      </div>
    </>
  )
}

/* ---------------------------------------------------------------------- */
/* 租户级管理（仅管理员）                                                  */
/* ---------------------------------------------------------------------- */

function ConnectionManager() {
  const queryClient = useQueryClient()
  const clients = useQuery({ queryKey: ['mcp-admin-clients'], queryFn: listMcpClients })
  const installations = useQuery({ queryKey: ['mcp-admin-installations'], queryFn: listMcpInstallations })
  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ['mcp-admin-clients'] }),
      queryClient.invalidateQueries({ queryKey: ['mcp-admin-installations'] }),
    ])
  const action = useMutation({
    mutationFn: async (input: { kind: 'installation' | 'client' | 'enable' | 'all'; id?: string }) => {
      if (input.kind === 'installation') return revokeMcpInstallation(input.id!)
      if (input.kind === 'client') return revokeMcpClient(input.id!)
      if (input.kind === 'enable') return enableMcpClient(input.id!)
      return revokeAllMcpInstallations()
    },
    onSuccess: refresh,
  })

  return (
    <div className={styles.managerBody}>
      <div className={styles.managerHead}>
        <p className={styles.muted}>查看本组织里谁连接了哪些助手。吊销后旧令牌立即失效，客户端也不能再次授权。</p>
        <button
          type="button"
          className={styles.dangerButton}
          disabled={action.isPending}
          onClick={() => window.confirm('确定撤销本组织的全部 AI 助手连接吗？') && action.mutate({ kind: 'all' })}
        >
          紧急撤销全部连接
        </button>
      </div>
      {action.isError && <p className={styles.error}>操作失败：{action.error.message}</p>}
      <div className={styles.managerGrid}>
        <div>
          <h3>已登记客户端</h3>
          {clients.isPending ? (
            <p className={styles.muted}>正在加载…</p>
          ) : clients.isError ? (
            <p className={styles.error}>客户端列表读取失败</p>
          ) : clients.data?.length ? (
            <ul className={styles.connectionList}>
              {clients.data.map(client => (
                <li key={client.client_id}>
                  <div>
                    <strong>{client.client_name}</strong>
                    <span>
                      {client.active_installations} 个活跃连接 · {client.registration_source}
                    </span>
                  </div>
                  <button
                    type="button"
                    disabled={action.isPending}
                    onClick={() => action.mutate({ kind: client.blocked ? 'enable' : 'client', id: client.client_id })}
                  >
                    {client.blocked ? '允许重新连接' : '封禁并吊销'}
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.muted}>暂无客户端。</p>
          )}
        </div>
        <div>
          <h3>授权实例</h3>
          {installations.isPending ? (
            <p className={styles.muted}>正在加载…</p>
          ) : installations.isError ? (
            <p className={styles.error}>授权实例读取失败</p>
          ) : installations.data?.length ? (
            <ul className={styles.connectionList}>
              {installations.data.map(item => (
                <li key={item.family_id}>
                  <div>
                    <strong>
                      {clients.data?.find(client => client.client_id === item.client_id)?.client_name ?? item.client_id}
                    </strong>
                    <span>
                      {item.user_name ?? `用户 ${item.user_id}`} · {item.role} ·{' '}
                      {item.active_refresh_tokens > 0 ? '有效' : '已撤销'}
                    </span>
                  </div>
                  {item.active_refresh_tokens > 0 && (
                    <button
                      type="button"
                      disabled={action.isPending}
                      onClick={() => action.mutate({ kind: 'installation', id: item.family_id })}
                    >
                      吊销
                    </button>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.muted}>暂无授权实例。</p>
          )}
        </div>
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------------- */
/* 页面                                                                    */
/* ---------------------------------------------------------------------- */

export function McpPage() {
  const queryClient = useQueryClient()
  const caps = useQuery({ queryKey: ['mcp-capabilities'], queryFn: getMcpCapabilities })
  // 连接管理中心的数据：最近调用、最近失败、近七天用量 —— 来自调用审计表，
  // 比 capabilities 里的静态实例列表多出"这个连接最近怎么样"这一层。
  const connections = useQuery({ queryKey: ['my-connections'], queryFn: getMyConnections })

  const [assistant, setAssistant] = useState<AssistantId>('codex')
  const [plan, setPlan] = useState('')
  // 用户是否**主动**选过权限套餐：默认选中的那一项不算"已选择"，
  // 否则第 1 步一进页面就是 ✓，那个状态点就没有信息量了。
  const [planTouched, setPlanTouched] = useState(false)
  // 是否复制过接入命令：用来把第 2 步标成"进行中"。复制只说明"开始动手了"，
  // 与"真的连上了"不是一回事 —— 后者只有第 3 步能证明。
  const [copiedOnce, setCopiedOnce] = useState(false)

  // 在线体验（试调）
  const [selected, setSelected] = useState<string>(INITIAL_TOOL)
  const [argsText, setArgsText] = useState<string>(JSON.stringify(EXAMPLES[INITIAL_TOOL], null, 2))
  const [result, setResult] = useState<McpToolResult | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [diag, setDiag] = useState<{
    phase: 'idle' | 'running' | 'done'
    results: Array<{ label: string; status: 'ok' | 'warn' | 'error'; text: string }>
  }>({ phase: 'idle', results: [] })
  const tryoutRef = useRef<HTMLDetailsElement>(null)

  if (caps.isPending) return <AsyncState kind="loading" title="正在读取可用工具" />
  if (caps.isError) return <AsyncState kind="error" title="可用工具读取失败" detail={caps.error.message} />
  const data = caps.data!

  const allTools = [...data.read_tools, ...data.write_tools]
  const selectedTool = allTools.find(tool => tool.name === selected)
  const isWriteSelected = selectedTool?.kind === 'write'
  const { fields, advancedOnly } = buildForm(selectedTool)
  const parsed = parseArgs(argsText)
  const args = parsed.ok ? parsed.args : {}
  const values = valuesFromArgs(fields, args)
  const copy = copyFor(selected)
  const summary = result ? describeResult(result) : null

  // 接入与否的**唯一事实源**：自己的安装实例。
  // `undefined`（字段缺席 = 读不到）与 `[]`（确实一个都没接）必须分开判断 ——
  // 合并它们会把"读不到"说成"你没有"，让用户去重做一个已经做过的接入。
  const mine = data.my_installations
  const hasInstallation = (mine?.length ?? 0) > 0
  // 连接管理中心的运行统计：family_id → 最近调用/最近失败/近七天用量。
  const callStats: Record<string, CallStats> = {}
  for (const item of connections.data?.installations ?? []) {
    callStats[item.family_id] = {
      last_call: item.last_call,
      last_failure: item.last_failure,
      calls_7d: item.calls_7d,
    }
  }
  const refreshMine = () => {
    // 两份查询来自同一批后端事实：断开后一起失效，否则列表更新了、
    // 「检查我的接入状态」给出的却还是旧结论。
    void queryClient.invalidateQueries({ queryKey: ['mcp-capabilities'] })
    void queryClient.invalidateQueries({ queryKey: ['my-connections'] })
  }
  const pickPlan = (key: string) => {
    setPlan(key)
    setPlanTouched(true)
  }
  const markCopied = () => setCopiedOnce(true)

  function pick(name: string, scroll = false) {
    const tool = allTools.find(candidate => candidate.name === name)
    setSelected(name)
    setArgsText(JSON.stringify(initialArgs(buildForm(tool).fields, EXAMPLES[name]), null, 2))
    setResult(null)
    setError('')
    // 卡片上的「在线体验」把用户带到试调区；jsdom 没有 scrollIntoView，用可选调用兜底。
    if (scroll) tryoutRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
  }

  function editField(key: string, raw: string) {
    // 参数只有一份：表单和「高级模式」编辑的是同一个对象，避免两处内容各说各话。
    const base = parseArgs(argsText)
    setArgsText(JSON.stringify(applyFieldValue(fields, base.ok ? base.args : {}, key, raw), null, 2))
  }

  async function run() {
    const current = parseArgs(argsText)
    if (!current.ok) {
      setError(current.error)
      return
    }
    if (isWriteSelected) {
      const caseId = typeof current.args.case_id === 'string' ? current.args.case_id.trim() : ''
      if (!caseId || caseId === CASE_ID_PLACEHOLDER) {
        setError('办理类必须先有案件编号：请把「案件编号」换成真实编号再提交。')
        return
      }
    }
    const missing = firstMissingRequired(fields, valuesFromArgs(fields, current.args))
    if (missing) {
      setError(`「${missing}」还没有填。`)
      return
    }
    setBusy(true)
    setError('')
    setResult(null)
    try {
      setResult(await callMcpTool(selected, current.args))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function checkAccess() {
    // 真实检测，分两层，回答的是两个不同的问题：
    // ① 后端聚合（调用审计）：**这个连接最近怎么样** —— 有没有接过、最近一次调用成不成功；
    // ② 现在真实调一次只读工具：**服务端此刻通不通** —— 把问题定位到"服务端"还是"助手侧"。
    // 措辞留在前端：结论码是后端给的事实，而"该叫 Codex 还是 AI 助手"只有前端知道。
    setDiag({ phase: 'running', results: [] })
    const collected: Array<{ label: string; status: 'ok' | 'warn' | 'error'; text: string }> = []

    let hasLive = false
    try {
      const report = await getMyConnections()
      const { code, live, installs, tool_count, failing } = report.check
      hasLive = live > 0
      if (code === 'no_installations') {
        collected.push({
          label: '接入状态',
          status: 'warn',
          text: '还没有接上任何助手。按上面的第 1～3 步做一次，接好之后会出现在这里。',
        })
      } else if (code === 'all_inactive') {
        collected.push({
          label: '接入状态',
          status: 'error',
          text: `接了 ${installs} 个，但都已经失效或被断开 —— 助手那边会调不通，需要重新连接。`,
        })
      } else if (code === 'no_calls_yet') {
        collected.push({
          label: '接入状态',
          status: 'warn',
          text: `已接上 ${live} 个，但还没有任何调用记录 —— 回到你的助手里问一句「试用期最长能多久」，能给出制度出处才算真的通了。`,
        })
      } else if (code === 'recent_failure') {
        collected.push({
          label: '接入状态',
          status: 'error',
          text: `已接上 ${live} 个，但最近有 ${failing} 个连接调用失败。如果助手那边一直报错，断开后重新连接一般能解决。`,
        })
      } else {
        collected.push({
          label: '接入状态',
          status: 'ok',
          text: `已接上 ${live} 个，最近一次调用成功。你的角色当前可用 ${tool_count} 个工具。`,
        })
      }
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      // 与第二层同一套归类：登录过期要给出可行动的说法，而不是透传原始错误 ——
      // 用户看到 "401" 只会茫然，看到"重新登录"才知道下一步。
      const expired = message.includes('登录') || message.includes('401')
      collected.push({
        label: '接入状态',
        status: expired ? 'warn' : 'error',
        text: expired ? '你的登录已过期，重新登录后再点一次。' : `暂时读不到连接状态：${message}`,
      })
    }
    setDiag({ phase: 'running', results: [...collected] })

    if (hasLive) {
      try {
        await callMcpTool('search_policy', { query: '连接诊断', top_k: 1 })
        collected.push({
          label: '服务端',
          status: 'ok',
          text: '刚用你的账号真实查了一次制度，服务端是正常的。如果助手那边还调不通，问题在助手的配置上。',
        })
      } catch (e) {
        const message = e instanceof Error ? e.message : ''
        const expired = message.includes('登录') || message.includes('401')
        collected.push({
          label: '服务端',
          status: expired ? 'warn' : 'error',
          text: expired ? '你的登录已过期，重新登录后再点一次。' : `服务端暂时不可用：${message}`,
        })
      }
    }
    setDiag({ phase: 'done', results: collected })
  }

  return (
    <main className="page-stack">
      <header className="page-heading">
        <div>
          <span className="eyebrow">AI 助手</span>
          <h1>把你自己的 AI 助手接上</h1>
          <p className="lede">
            完成一次连接后，就可以在你常用的 AI 助手里查询制度、发起 HR 办理。
            个人环境通常可自行完成；如果公司统一管理 AI 工具，可能需要管理员允许。连接可以随时断开。
          </p>
        </div>
        <div className="admin-links">
          <button
            type="button"
            className="primary-button"
            onClick={() => void checkAccess()}
            disabled={diag.phase === 'running'}
          >
            {diag.phase === 'running' ? '正在检查…' : '检查我的接入状态'}
          </button>
        </div>
      </header>

      {(diag.phase === 'running' || diag.phase === 'done') && (
        <section className={styles.checkup} aria-live="polite" aria-label="连接检查结果">
          {diag.phase === 'running' && diag.results.length === 0 && (
            <p className={styles.checkupRunning}>正在检查身份和权限…</p>
          )}
          {diag.results.length > 0 && (
            <ul className={styles.checkupList}>
              {diag.results.map(step => (
                <li key={step.label}>
                  <strong>{step.label}</strong>
                  <span className={step.status === 'ok' ? styles.checkupOk : styles.checkupWarn}>{step.text}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {/* ================= 首屏：按接入状态二选一 =================
          用户打开这页只有两种处境，第一屏必须直接回答他所处的那一种：
          还没接 → 给他三步；已经接了 → 告诉他"接好了、怎么验证、在哪解绑"。
          改版前不论哪种处境都铺三步，于是已经接上的人被引导着**又接了一遍** ——
          而每次授权都会新建一条独立的链，「我接过的助手」里就会多出一个同名条目，
          用户也就分不清哪条在用。 */}
      {hasInstallation ? (
        <section className="panel" aria-label="已接上的助手">
          <h2>你已经有接上的助手</h2>
          <p className={styles.muted}>
            平时不用回到这个页面 —— 直接在你自己的助手里说就行。下面是它们的状态；想停用其中任何一个，点「断开连接」。
          </p>
          {connections.isError && (
            // 运行统计读不到只影响"最近怎么样"这一层，身份与权限（capabilities）仍在 ——
            // 所以报一句就好，不把整页降级成错误。
            <p className={styles.error}>最近调用情况暂时读不到，稍后刷新再看。</p>
          )}
          <MyAssistants installations={mine ?? []} callStats={callStats} onRevoked={refreshMine} />

          <h3 style={{ marginTop: 'var(--space-6)' }}>开始使用</h3>
          <p className={styles.muted}>
            在你的助手里直接说，例如「公司的年假是怎么规定的？」「把张三的试用期跟进转给李经理」。助手不会直接改数据：
            办理会先给你一张确认卡，你确认后进入 HR 审批，审批通过才会执行。
          </p>
          <p className={styles.muted}>
            想确认真的通了，就问一句「试用期最长能多久」—— 能给出制度出处和原文，就说明通了。提交过的办理可以在{' '}
            <Link to="/tasks?tab=agent">任务中心 → AI 发起</Link> 里看进度。
          </p>

          <details className={styles.details}>
            <summary>再接一个别的助手</summary>
            <div className={styles.detailsBody}>
              <InstallSteps
                data={data}
                assistant={assistant}
                onPickAssistant={setAssistant}
                plan={plan}
                onPickPlan={pickPlan}
                hasInstallation={hasInstallation}
                planTouched={planTouched}
                copiedOnce={copiedOnce}
                onCopied={markCopied}
              />
            </div>
          </details>
        </section>
      ) : (
        <section className="panel" aria-label="接入步骤">
          {mine === undefined && (
            // 字段缺席（读不到）与"确实一个都没接"是两件事。这里不谎称"你还没接"，
            // 只是把话说清楚；三步仍然铺出来，因为真没接的人正需要它。
            <p className={styles.muted}>暂时读不到你接过的助手；如果你其实已经接过，刷新页面再看一次。</p>
          )}
          <InstallSteps
            data={data}
            assistant={assistant}
            onPickAssistant={setAssistant}
            plan={plan}
            onPickPlan={pickPlan}
            hasInstallation={hasInstallation}
            planTouched={planTouched}
            copiedOnce={copiedOnce}
            onCopied={markCopied}
          />
        </section>
      )}

      {/* ================= 接好之后 ================= */}
      <section className="panel" aria-label="接好之后怎么用">
        <h2>接好之后怎么用</h2>
        <p className={styles.muted}>之后不用再打开这个页面，在自己的助手里说就行。</p>

        <h3>查资料 —— 随时可用</h3>
        <p className={styles.muted}>
          直接问，它会把制度原文和出处一起给你。例如「公司的年假是怎么规定的？」「试用期最长能多久？」
        </p>

        {/* 「办事情」默认收起：它是接上之后第二步才会用到的事，摊在同一屏里只会让页面更长，
            而对此刻刚接好的人来说，第一段「查资料」就是他要的答案。 */}
        <details className={styles.details}>
          <summary>办事情 —— 会先给你一张确认卡</summary>
          <div className={styles.detailsBody}>
            <p className={styles.muted}>
              比如「把张三的试用期跟进转给李经理」。助手<b>不会直接改数据</b>：它先把要做什么列成确认卡，
              你核对后点头才进入 HR 审批，审批通过才会执行。
            </p>
          </div>
        </details>
      </section>

      {/* ================= 联调工具（默认收起） ================= */}
      <details className={styles.details} ref={tryoutRef}>
        <summary>联调工具（给开发同学）</summary>
        <div className={styles.detailsBody}>
          <h3>在线试调</h3>
          <p className={styles.muted}>
            选一个工具、填入内容，点按钮就能看到真实返回 —— 和你通过 AI 助手调用走的是同一套规则。
          </p>
          <div className={styles.tryLayout}>
            <div className={styles.field}>
              <label>
                <span>选择工具</span>
                <select value={selected} onChange={e => pick(e.target.value)}>
                  <optgroup label="查询类 · 只查不改">
                    {data.read_tools.map(tool => (
                      <option key={tool.name} value={tool.name}>
                        {copyFor(tool.name).label}
                      </option>
                    ))}
                  </optgroup>
                  <optgroup label="办理类 · 需 HR 批准">
                    {data.write_tools.map(tool => (
                      <option key={tool.name} value={tool.name}>
                        {copyFor(tool.name).label}
                      </option>
                    ))}
                  </optgroup>
                </select>
              </label>
              <p className={styles.tryWhat}>{copy.what}</p>
              <p className={styles.toolSay}>
                <span>可以这样对 AI 说</span>「{copy.say}」
              </p>
            </div>

            <div className={styles.field}>
              <span>填写内容</span>
              {fields.length === 0 ? (
                <p className={styles.muted}>这个工具不需要填任何内容，直接点下方按钮即可。</p>
              ) : (
                <div className={styles.formGrid}>
                  {fields.map(field => (
                    <FieldControl
                      key={field.key}
                      field={field}
                      value={values[field.key] ?? ''}
                      onChange={next => editField(field.key, next)}
                    />
                  ))}
                </div>
              )}
              {advancedOnly.length > 0 && (
                <p className={styles.formHelp}>
                  这个工具还有表单装不下的内容（{advancedOnly.join('、')}），需要时请到下方「高级模式」里补充。
                </p>
              )}
              <details className={styles.details}>
                <summary>高级模式：直接编辑参数</summary>
                <div className={styles.detailsBody}>
                  <p className={styles.muted}>
                    只有当你需要填的内容在上面的表单里找不到时才用这里。这里的内容就是最终提交的内容。
                  </p>
                  <textarea
                    value={argsText}
                    onChange={e => setArgsText(e.target.value)}
                    rows={10}
                    spellCheck={false}
                    aria-label="参数"
                  />
                  {!parsed.ok && <p className={styles.error}>{parsed.error}</p>}
                </div>
              </details>
            </div>
          </div>
          <div className={styles.tryActions}>
            {/* 办理类工具在这里产生的是**真实审批单**（与助手提交走同一条审批流程）——
                必须说在按下之前，否则审批人会凭空收到一条"网页试出来的办理"。 */}
            {isWriteSelected && <p className={styles.muted}>提交后会产生真实审批单，进入 HR 审批流程后才执行。</p>}
            <button type="button" className="primary-button" onClick={run} disabled={busy || !parsed.ok}>
              {busy ? '处理中…' : isWriteSelected ? '提交办理' : '开始查询'}
            </button>
            {error && <span className={styles.error}>{error}</span>}
          </div>
          {summary && result && (
            <div className={styles.result}>
              <p
                className={
                  summary.tone === 'ok'
                    ? styles.resultOk
                    : summary.tone === 'warn'
                      ? styles.resultWarn
                      : styles.resultError
                }
              >
                {summary.text}
              </p>
              <details className={styles.details}>
                <summary>查看原始返回数据</summary>
                <div className={styles.detailsBody}>
                  <pre className={styles.raw}>{JSON.stringify(result, null, 2)}</pre>
                </div>
              </details>
            </div>
          )}

          <h3 style={{ marginTop: 'var(--space-6)' }}>可用工具（共 {allTools.length} 个）</h3>
          {data.hidden_tool_count > 0 && (
            // 说清"为什么这里比别人少几个"就够了。不写"请联系管理员"：这一页面向的是
            // 使用者本人，而"联系谁"因人而异，写死一个称谓反而给出错误的下一步。
            <p className={styles.hiddenNote}>
              你当前的角色只能用上面这些；另有 {data.hidden_tool_count} 个工具因权限范围不同未列出。
            </p>
          )}
          <div className={styles.catalog}>
            <h4 className={styles.groupTitle}>查询类 · 只查不改</h4>
            <div className={styles.cardGrid}>
              {data.read_tools.map(tool => (
                <ToolCard key={tool.name} tool={tool} onTry={name => pick(name, true)} />
              ))}
            </div>
            <h4 className={styles.groupTitle}>办理类 · 需 HR 批准</h4>
            <div className={styles.cardGrid}>
              {data.write_tools.map(tool => (
                <ToolCard key={tool.name} tool={tool} onTry={name => pick(name, true)} />
              ))}
            </div>
          </div>
        </div>
      </details>

      {/* ================= 租户级管理（仅管理员可见） ================= */}
      {data.role === 'admin' && (
        <details className="panel">
          <summary>租户级管理（仅管理员可见）</summary>
          <ConnectionManager />
        </details>
      )}
    </main>
  )
}
