#!/usr/bin/env bash
# 在 macOS 原生终端运行：把本机 HRBPilot 接入本机 Codex CLI。
# 不要从受隔离的 CI / Agent 沙箱运行：OAuth 的 loopback 回调必须能回到同一台主机。
#
# 套餐必须显式选择：
#   1 = 仅查询
#   2 = 查询 + 提交办理建议（写工具只创建审批，不直接修改业务数据）

set -euo pipefail

server_name="${HRBPILOT_MCP_NAME:-hrbpilot-tasks}"
resource_url="${HRBPILOT_MCP_URL:-http://localhost:8001/mcp/tasks}"
ready_url="${resource_url%%/mcp*}/api/ready"
tier=""
non_interactive=false

fail() {
  printf '错误：%s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
用法：connect-codex-local-mcp.sh [--tier 1|2] [--non-interactive]

  --tier 1             仅查询：身份、制度、案件、审批
  --tier 2             查询 + 提交办理建议（写操作进入审批）
  --non-interactive    禁止交互；必须同时显式传入 --tier
EOF
}

while (($#)); do
  case "$1" in
    --tier)
      (($# >= 2)) || fail '--tier 缺少值（只能是 1 或 2）。'
      tier="$2"
      shift 2
      ;;
    --non-interactive)
      non_interactive=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "未知参数：$1"
      ;;
  esac
done

if command -v codex >/dev/null 2>&1; then
  codex_cli="$(command -v codex)"
elif [[ -x /Applications/ChatGPT.app/Contents/Resources/codex ]]; then
  # macOS 桌面版随附 CLI，但安装程序未必把它写入用户的 PATH。
  codex_cli=/Applications/ChatGPT.app/Contents/Resources/codex
else
  fail '未找到 Codex CLI。请安装官方 CLI：curl -fsSL https://chatgpt.com/codex/install.sh | sh'
fi
command -v curl >/dev/null 2>&1 || fail '未找到 curl。'

printf '使用 Codex CLI：%s\n' "$codex_cli"

if ! curl --fail --silent --show-error "$ready_url" >/dev/null; then
  fail "HRBPilot 尚未就绪（$ready_url）。请先在本项目目录执行：docker compose up -d --build"
fi

if [[ -z "$tier" ]]; then
  [[ "$non_interactive" == false ]] || fail 'non-interactive 模式必须用 --tier 显式指定套餐（1 或 2）。'
  read -r -p '选择授权套餐：1=仅查询 2=查询+提交办理建议（输入 1 或 2）：' tier
fi

case "$tier" in
  1)
    scopes=(
      hrb:profile:read
      hrb:policy:read
      hrb:case:read
      hrb:approval:read
    )
    ;;
  2)
    scopes=(
      hrb:profile:read
      hrb:policy:read
      hrb:case:read
      hrb:approval:read
      hrb:case:propose
    )
    ;;
  *)
    fail '授权套餐只能是 1 或 2。'
    ;;
esac
scope_csv="$(IFS=,; printf '%s' "${scopes[*]}")"
printf '将申请 scope：%s\n' "${scopes[*]}"

if "$codex_cli" mcp get "$server_name" >/dev/null 2>&1; then
  printf '复用已有 Codex MCP 配置：%s\n' "$server_name"
else
  printf '添加本机 MCP 服务：%s → %s\n' "$server_name" "$resource_url"
  "$codex_cli" mcp add "$server_name" \
    --url "$resource_url" \
    --oauth-client-registration dcr
fi

cat <<EOF

即将打开浏览器授权页。请在同一台 Mac 上登录 HRBPilot 并点击“授权”。
Safari 或 Chrome 都可以；关键是本脚本必须在 macOS 原生终端运行。
EOF

"$codex_cli" mcp login "$server_name" --scopes "$scope_csv"

cat <<EOF

授权流程完成。请在 Codex 中执行两项验证后再把连接视为可用：
  1. 调用 get_my_access_profile，确认 effective_scopes 包含：${scopes[*]}
  2. 调用 answer_policy_question 查询「公司的年假是怎么规定的」，确认返回制度出处。

移除本机连接：
  codex mcp logout "$server_name"
  codex mcp remove "$server_name"
EOF
