import { apiClient } from './http'; import type { UnknownRecord } from './types'
export const getMetrics=()=>apiClient.request<{scenarios:UnknownRecord[]|Record<string,UnknownRecord>}>('/api/eval/metrics')
export const getScenarioMetrics=(scenarioId:string)=>apiClient.request<{scenario_id:string;metrics:UnknownRecord;message?:string}>(`/api/eval/metrics/${scenarioId}`)
export const getScenarioTrend=(scenarioId:string,metric:string,days=7)=>apiClient.request<{data:UnknownRecord[]}>(`/api/eval/metrics/${scenarioId}/trend?metric=${encodeURIComponent(metric)}&days=${days}`)
