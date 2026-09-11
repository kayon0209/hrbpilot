import { apiClient } from './http'
import type { Readiness } from './types'

export const getReadiness = () => apiClient.probe<Readiness>('/api/ready', [503])
