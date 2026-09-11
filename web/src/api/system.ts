import { apiClient } from './http'
import type { Readiness } from './types'

/**
 * 503 是 readiness 的合法结论（关键依赖故障），不是请求失败 ——
 * 响应体里带着「哪一项依赖不可用」，正是管理页要展示的内容。
 * 若在这里抛错，管理页只会显示「无法读取系统状态」，恰好丢掉最关键的信息。
 */
export const getReadiness = () => apiClient.probe<Readiness>('/api/ready', [503])
