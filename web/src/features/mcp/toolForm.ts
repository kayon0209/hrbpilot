/**
 * 由工具的参数结构生成中文业务表单。
 *
 * 唯一事实源是后端的 `TOOL_CATALOG`（随 `/api/mcp/capabilities` 返回的
 * `input_schema`）。这里只做"翻译"：字段类型 → 输入控件，字段名 → 中文标签
 * （词典在 toolCopy.ts）。刻意**不**在前端另维护一份字段表 —— 那会立刻和
 * 工具定义漂移，而工具定义已经是全系统唯一事实源。
 *
 * 无法用表单表达的结构（object / array，目前只有通知模板的 params）不做猜测，
 * 记进 `advancedOnly` 并提示用户用「高级模式」补写。
 */
import type { McpToolView } from '../../api/mcp'
import { FIELD_LABELS, OPTION_LABELS } from './toolCopy'

export interface FormField {
  key: string
  label: string
  required: boolean
  kind: 'text' | 'number' | 'datetime' | 'select'
  options?: Array<{ value: string; label: string }>
  defaultValue?: string
}

export interface ToolForm {
  fields: FormField[]
  /** 表单装不下、只能在高级模式里写的字段名（object / array）。 */
  advancedOnly: string[]
}

interface SchemaNode {
  type?: string
  format?: string
  enum?: unknown[]
  const?: unknown
  pattern?: string
  anyOf?: SchemaNode[]
  default?: unknown
  properties?: Record<string, SchemaNode>
  required?: string[]
}

function resolveNode(node: SchemaNode): SchemaNode {
  if (node.anyOf?.length) {
    return node.anyOf.find(candidate => candidate.type && candidate.type !== 'null') ?? node
  }
  return node
}

/** 固定取值：const / enum / 单值 pattern / 枚举式 pattern（^(A|B)$）。 */
function fixedValues(node: SchemaNode): string[] {
  if (typeof node.const === 'string') return [node.const]
  if (Array.isArray(node.enum) && node.enum.every(v => typeof v === 'string')) return node.enum as string[]
  const single = node.pattern?.match(/^\^([A-Za-z0-9_]+)\$$/)
  if (single) return [single[1]]
  const alternation = node.pattern?.match(/^\^\(([A-Za-z0-9_|]+)\)\$$/)
  if (alternation) return alternation[1].split('|')
  return []
}

export function buildForm(tool: McpToolView | undefined): ToolForm {
  if (!tool) return { fields: [], advancedOnly: [] }
  const schema = tool.input_schema as SchemaNode
  const properties = schema.properties ?? {}
  const required = new Set(schema.required ?? [])
  const fields: FormField[] = []
  const advancedOnly: string[] = []

  // 办理类多一个"案件编号"：它是审批的挂载点，属于调用约定而不在参数结构里
  // （后端 /api/mcp/tools/{name}/call 会在调用前单独要求它）。
  if (tool.kind === 'write') {
    fields.push({ key: 'case_id', label: FIELD_LABELS.case_id ?? 'case_id', required: true, kind: 'text' })
  }

  for (const [key, rawNode] of Object.entries(properties)) {
    const node = resolveNode(rawNode)
    if (node.type === 'object' || node.type === 'array') {
      advancedOnly.push(key)
      continue
    }

    const label = FIELD_LABELS[key] ?? key
    const isRequired = required.has(key)
    const fixed = fixedValues(node)

    if (fixed.length > 0) {
      // 跟服务端默认值保持一致：schema 给的默认值能对上某个固定取值时直接选中，
      // 让用户看见"不填会变成什么"，而不是提交后才发现。
      const schemaDefault =
        typeof node.default === 'string' && fixed.includes(node.default) ? node.default : undefined
      fields.push({
        key,
        label,
        required: isRequired,
        kind: 'select',
        options: fixed.map(value => ({ value, label: OPTION_LABELS[value] ?? value })),
        defaultValue: schemaDefault ?? (fixed.length === 1 ? fixed[0] : undefined),
      })
      continue
    }

    if (node.type === 'integer' || node.type === 'number') {
      fields.push({
        key,
        label,
        required: isRequired,
        kind: 'number',
        defaultValue: typeof node.default === 'number' ? String(node.default) : undefined,
      })
      continue
    }

    if (node.format === 'date-time') {
      fields.push({ key, label, required: isRequired, kind: 'datetime' })
      continue
    }

    fields.push({
      key,
      label,
      required: isRequired,
      kind: 'text',
      defaultValue: typeof node.default === 'string' && node.default ? node.default : undefined,
    })
  }

  return { fields, advancedOnly }
}

/**
 * 表单初值：先用示例内容填满（让用户一点开就有能跑通的内容），再补 schema 默认值。
 *
 * 返回的是**控件显示用的字符串**，不是调用参数 —— 参数由 `initialArgs` /
 * `applyFieldValue` 生成。
 */
function exampleValues(fields: FormField[], example?: Record<string, unknown>): Record<string, string> {
  const values: Record<string, string> = {}
  for (const field of fields) {
    const raw = example?.[field.key]
    if (typeof raw === 'string' || typeof raw === 'number') {
      values[field.key] = String(raw)
    } else {
      values[field.key] = field.defaultValue ?? ''
    }
  }
  return values
}

/**
 * 打开某个工具时的初始参数。
 *
 * 顺序很讲究：先放示例里"表单装不下"的字段（如通知 params），再覆盖表单字段 ——
 * 表单字段是用户看得见、能改的那部分，不能被示例悄悄盖回去。
 */
export function initialArgs(fields: FormField[], example: Record<string, unknown> | undefined): Record<string, unknown> {
  const formKeys = new Set(fields.map(field => field.key))
  const extras: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(example ?? {})) {
    if (!formKeys.has(key)) extras[key] = value
  }
  return { ...extras, ...buildArgs(fields, exampleValues(fields, example)) }
}

/**
 * 参数对象 → 调用参数（丢弃空值）。
 *
 * 空值一律不发：让服务端套用 schema 默认值。否则 `""` 会撞上
 * `min_length=1` 之类的校验，用户看到的是一个本可避免的报错。
 */
export function buildArgs(fields: FormField[], values: Record<string, string>): Record<string, unknown> {
  const args: Record<string, unknown> = {}
  for (const field of fields) {
    const raw = (values[field.key] ?? '').trim()
    if (raw === '') continue
    if (field.kind === 'number') {
      const parsed = Number(raw)
      if (Number.isFinite(parsed)) args[field.key] = parsed
      continue
    }
    args[field.key] = raw
  }
  return args
}

/** 参数对象 → 控件显示用的字符串值。 */
export function valuesFromArgs(fields: FormField[], args: Record<string, unknown>): Record<string, string> {
  const values: Record<string, string> = {}
  for (const field of fields) {
    const raw = args[field.key]
    values[field.key] = raw === undefined || raw === null ? '' : String(raw)
  }
  return values
}

/** 单个字段改值 → 新的参数对象（清空即删除该键，交由服务端取默认值）。 */
export function applyFieldValue(
  fields: FormField[],
  args: Record<string, unknown>,
  key: string,
  raw: string,
): Record<string, unknown> {
  const next = { ...args }
  if (raw.trim() === '') {
    delete next[key]
    return next
  }
  const field = fields.find(candidate => candidate.key === key)
  if (field?.kind === 'number') {
    const parsed = Number(raw.trim())
    // 数字框里塞了非数字就原样留着：让后端给出比前端更准确的校验说明。
    next[key] = Number.isFinite(parsed) ? parsed : raw
    return next
  }
  next[key] = raw
  return next
}

/** 解析「高级模式」里的参数文本。 */
export function parseArgs(text: string): { ok: true; args: Record<string, unknown> } | { ok: false; error: string } {
  const trimmed = text.trim()
  if (trimmed === '') return { ok: true, args: {} }
  try {
    const parsed: unknown = JSON.parse(trimmed)
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { ok: false, error: '参数需要写成 { "字段": "内容" } 的样子。' }
    }
    return { ok: true, args: parsed as Record<string, unknown> }
  } catch {
    return { ok: false, error: '参数内容不完整，请检查引号和括号是否成对。' }
  }
}

/** 第一个没填的必填项的中文标签；都填了就返回 null。 */
export function firstMissingRequired(fields: FormField[], values: Record<string, string>): string | null {
  for (const field of fields) {
    if (field.required && !(values[field.key] ?? '').trim()) return field.label
  }
  return null
}
