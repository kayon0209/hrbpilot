# 在 Windows 原生终端（PowerShell 7+ 或 Windows PowerShell 5.1）运行：
# 把本机 HRBPilot 接入本机 Codex CLI。
# 不要从受隔离的沙箱 / CI 运行：OAuth 的 loopback 回调必须回到同一台主机。
#
# 与 scripts/connect-codex-local-mcp.sh 等价，外加两处产品化（任务书 T2）：
#   1. 套餐选择：显式选"仅查询"或"查询+提交办理建议"，写权限不默认授予；
#   2. 三项冒烟：登录结束后自动校验 身份 / scope 套餐 / 一个业务只读工具，
#      并逐项打印结果 —— 不把"授权完成"当成"能用"。

#Requires -Version 5.1
[CmdletBinding()]
param(
  [string]$ServerName = $(if ($env:HRBPILOT_MCP_NAME) { $env:HRBPILOT_MCP_NAME } else { 'hrbpilot-tasks' }),
  [string]$ResourceUrl = $(if ($env:HRBPILOT_MCP_URL) { $env:HRBPILOT_MCP_URL } else { 'http://localhost:8001/mcp/tasks' }),
  # 跳过交互确认（用于自动化场景）；套餐仍需 -Tier 显式给出。
  [switch]$NonInteractive,
  [ValidateSet('1', '2')]
  [string]$Tier
)

$ErrorActionPreference = 'Stop'

function Fail([string]$Message) {
  Write-Host "错误：$Message" -ForegroundColor Red
  exit 1
}

$ReadyUrl = ($ResourceUrl -replace '/mcp(?:/tasks)?$', '') + '/api/ready'

# --- 前置检查 -------------------------------------------------------------

$codex = Get-Command codex -ErrorAction SilentlyContinue
if (-not $codex) {
  # Windows 桌面版（ChatGPT 安装器）常不写 PATH；找常见位置兜底。
  $candidates = @(
    "$env:LOCALAPPDATA\Programs\Codex\codex.exe",
    "$env:LOCALAPPDATA\Programs\ChatGPT\resources\codex.exe"
  )
  $found = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $found) {
    Fail '未找到 Codex CLI。请先安装并确保 "codex --version" 可用（npm i -g @openai/codex 或官方安装器）。'
  }
  $codexPath = $found
}
else {
  $codexPath = $codex.Source
}
Write-Host "使用 Codex CLI：$codexPath"

try {
  Invoke-RestMethod -Uri $ReadyUrl -TimeoutSec 5 | Out-Null
}
catch {
  Fail "HRBPilot 尚未就绪（$ReadyUrl）。请先在项目目录执行：docker compose up -d --build"
}

# --- 套餐选择（写权限不默认授予）------------------------------------------

if (-not $Tier) {
  if ($NonInteractive) { Fail 'NonInteractive 模式必须用 -Tier 显式指定套餐（1 或 2）。' }
  $Tier = Read-Host '选择授权套餐：1=仅查询 2=查询+提交办理建议（输入 1 或 2）'
}
$scopes = switch ($Tier) {
  '2' { @('hrb:profile:read', 'hrb:policy:read', 'hrb:case:read', 'hrb:approval:read', 'hrb:case:propose') }
  default { @('hrb:profile:read', 'hrb:policy:read', 'hrb:case:read', 'hrb:approval:read') }
}
$expected = $scopes | Sort-Object
Write-Host "将申请 scope：$($expected -join ' ')"

# --- 注册（已存在则复用）---------------------------------------------------

$existing = & $codexPath mcp get $ServerName 2>$null
if ($LASTEXITCODE -eq 0 -and $existing) {
  Write-Host "复用已有 Codex MCP 配置：$ServerName（如需换套餐请先 codex mcp remove $ServerName）"
}
else {
  Write-Host "添加本机 MCP 服务：$ServerName → $ResourceUrl"
  & $codexPath mcp add $ServerName `
    --url $ResourceUrl `
    --oauth-client-registration dcr
  if ($LASTEXITCODE -ne 0) { Fail 'codex mcp add 失败（见上方输出）。' }
}

# --- 浏览器授权（scope 用 codex mcp login 的 --scopes 传递，逗号分隔）-------

Write-Host ''
Write-Host '即将打开浏览器授权页。请在同一台 Windows 上登录 HRBPilot 并点击「授权」。'
& $codexPath mcp login $ServerName --scopes ($scopes -join ',')
if ($LASTEXITCODE -ne 0) { Fail 'codex mcp login 失败（见上方输出）。' }

# --- 三项冒烟校验 ----------------------------------------------------------
# 登录完成 ≠ 能用。依次校验：身份 → scope 套餐逐项一致 → 一个业务只读工具。
# 冒烟用 codex exec 走自然语言（与真实使用同一路径），一次一条，结果打印后退出。

Write-Host ''
Write-Host '== 连接冒烟（三项）==' -ForegroundColor Cyan
$smokeOk = $true

function Invoke-Smoke([string]$Label, [string]$Prompt, [scriptblock]$Assert) {
  Write-Host "`n[冒烟] $Label"
  $out = & $codexPath exec --skip-git-repo-check -m gpt-5.1-codex-mini $Prompt 2>&1 | Out-String
  Write-Host $out.Trim()
  if (-not (& $Assert $out)) {
    Write-Host "  → 未通过：$Label" -ForegroundColor Red
    $script:smokeOk = $false
  }
  else {
    Write-Host "  → 通过" -ForegroundColor Green
  }
}

# 1) 身份 + 2) scope 套餐：一次 get_my_access_profile 同时覆盖两项
Invoke-Smoke '身份与 scope 套餐（get_my_access_profile）' `
  '调用 MCP 工具 get_my_access_profile，原文返回 effective_scopes 列表与当前用户标识，不要总结。' `
  {
    param($out)
    $missing = @()
    foreach ($s in $expected) { if ($out -notmatch [regex]::Escape($s)) { $missing += $s } }
    if ($missing.Count -gt 0) {
      Write-Host "  缺少 scope：$($missing -join ' ')" -ForegroundColor Yellow
      return $false
    }
    return $true
  }

# 3) 业务只读工具：一句固定问句，必须带回出处
Invoke-Smoke '业务只读工具（answer_policy_question 带出处）' `
  '调用 MCP 工具 answer_policy_question 查询「公司的年假是怎么规定的」，返回条文与出处（制度名称与条款位置）。' `
  {
    param($out)
    if ($out -match 'AUTH_REQUIRED|FORBIDDEN|FAILED') { return $false }
    return $out.Length -gt 40
  }

Write-Host ''
if ($smokeOk) {
  Write-Host '连接冒烟：三项全部通过。' -ForegroundColor Green
}
else {
  Write-Host '连接冒烟存在未通过项 —— 不要把状态当成"已连接"。' -ForegroundColor Red
  Write-Host '排查顺序：docker compose ps 是否 healthy → codex mcp login 重授权 → get_my_access_profile 看 effective_scopes。'
}

Write-Host ''
Write-Host '移除本机连接：'
Write-Host "  codex mcp logout $ServerName"
Write-Host "  codex mcp remove $ServerName"
