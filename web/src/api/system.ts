import { apiClient } from './http'
import type { Readiness } from './types'

export const getReadiness = () => apiClient.request<Readiness>('/api/ready')
