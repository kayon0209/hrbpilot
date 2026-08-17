import { apiClient } from './http'
export interface ProviderInfo{id:string;name?:string;configured?:boolean;model?:string}
export const getProvider=()=>apiClient.request<{providers:ProviderInfo[];active:string;active_model:string}>('/api/settings/llm-provider')
export const testProvider=()=>apiClient.request<{status:string;provider:string;model:string;response?:string;tokens?:number;error?:string}>('/api/settings/llm-provider/test')
export const switchProvider=(provider:string)=>apiClient.request<{status:string;active?:string;active_model?:string;message?:string}>('/api/settings/llm-provider',{method:'POST',body:JSON.stringify({provider})})
