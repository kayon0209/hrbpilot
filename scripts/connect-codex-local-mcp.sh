#!/usr/bin/env bash
# 在 macOS 原生终端运行：把本机 HRBPilot 接入本机 Codex CLI。
# 不要从受隔离的 CI / Agent 沙箱运行：OAuth 的 loopback 回调必须能回到同一台主机。

set -euo pipefail

server_name="${HRBPILOT_MCP_NAME:-hrbpilot-local}"
resource_url="${HRBPILOT_MCP_URL:-http://localhost:8001/mcp}"
ready_url="${resource_url%/mcp}/api/ready"

fail() {
  printf '错误：%s\n' "$*" >&2
  exit 1
}

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

if "$codex_cli" mcp get "$server_name" >/dev/null 2>&1; then
  printf '复用已有 Codex MCP 配置：%s\n' "$server_name"
else
  printf '添加本机 MCP 服务：%s → %s\n' "$server_name" "$resource_url"
  "$codex_cli" mcp add "$server_name" \
    --url "$resource_url" \
    --oauth-client-registration dcr \
    --oauth-resource "$resource_url"
fi

cat <<EOF

即将打开浏览器授权页。请在同一台 Mac 上登录 HRBPilot 并点击“授权”。
Safari 或 Chrome 都可以；关键是本脚本必须在 macOS 原生终端运行。
EOF

"$codex_cli" mcp login "$server_name"

cat <<EOF

连接完成。建议先在 Codex 中执行只读验证：
  使用 MCP 服务 $server_name 调用 get_my_access_profile；不要读取文件或修改数据。

移除本机连接：
  codex mcp logout "$server_name"
  codex mcp remove "$server_name"
EOF
