"""Windows development connection and validation helper for the task gateway.

Use from PowerShell 7 after Compose is healthy. It intentionally does not store
tokens, authorization codes, or response bodies. Each smoke test prints only a
bounded, redacted result:

1. identity/capability profile;
2. task-bundle discovery and authorization scope;
3. a read-only policy call;
4. an intent draft call (no business write), proving the frozen-draft layer.

Codex / WorkBuddy still perform their own real browser OAuth acceptance; this
script verifies the local HTTP contract without pretending a client approval is
the same as a server authorization.
"""

[CmdletBinding()]
param(
  [string]$BaseUrl = $(if ($env:HRBPILOT_BASE_URL) { $env:HRBPILOT_BASE_URL } else { 'http://localhost:8001' }),
  [string]$AccessToken = $(if ($env:HRBPILOT_ACCESS_TOKEN) { $env:HRBPILOT_ACCESS_TOKEN } else { '' }),
  [switch]$AllowWriteDraftSmoke
)

$ErrorActionPreference = 'Stop'
$BaseUrl = $BaseUrl.TrimEnd('/')

function Invoke-Json([string]$Method, [string]$Path, [object]$Body = $null) {
  $headers = @{}
  if ($AccessToken) { $headers['Authorization'] = "Bearer $AccessToken" }
  $params = @{ Method = $Method; Uri = "$BaseUrl$Path"; Headers = $headers; TimeoutSec = 15 }
  if ($null -ne $Body) {
    $params['ContentType'] = 'application/json'
    $params['Body'] = ($Body | ConvertTo-Json -Depth 8 -Compress)
  }
  try { return Invoke-RestMethod @params }
  catch {
    $message = $_.Exception.Message -replace '(?i)bearer\s+[^\s,]+', 'Bearer [redacted]'
    throw "HTTP $Method $Path failed: $message"
  }
}

function Show-Result([string]$Name, [object]$Value) {
  $json = ($Value | ConvertTo-Json -Depth 8 -Compress)
  if ($json.Length -gt 800) { $json = $json.Substring(0, 800) + '…[truncated]' }
  Write-Host "[$Name] $json"
}

Write-Host "HRBPilot task gateway smoke: $BaseUrl"
$ready = Invoke-Json 'GET' '/api/ready'
Show-Result 'ready' $ready

if (-not $AccessToken) {
  Write-Warning '未提供 HRBPILOT_ACCESS_TOKEN：只运行匿名发现检查，不声称业务 smoke 通过。'
  $metadata = Invoke-Json 'GET' '/.well-known/oauth-protected-resource'
  Show-Result 'resource_metadata' $metadata
  exit 2
}

$profile = Invoke-Json 'POST' '/api/mcp/tools/get_my_access_profile/call' @{ arguments = @{} }
Show-Result 'identity_profile' $profile

$caps = Invoke-Json 'GET' '/api/mcp/capabilities'
Show-Result 'task_gateway' $caps.task_gateway

$policy = Invoke-Json 'POST' '/api/mcp/tools/search_policy/call' @{ arguments = @{ query = '公司的年假是怎么规定的'; top_k = 1 } }
Show-Result 'policy_read' $policy

if ($AllowWriteDraftSmoke) {
  $draft = Invoke-Json 'POST' '/api/agent-tasks/prepare' @{ goal = '帮我建立一个试用期跟进任务' }
  Show-Result 'frozen_draft' $draft
  if ($draft.status -notin @('input_required', 'ready_for_confirmation')) {
    throw "prepare smoke returned unexpected status: $($draft.status)"
  }
}

Write-Host '本地任务网关 smoke 结束：身份、能力与只读调用均已执行；写入仍需真实客户端确认和 HR 审批。'
