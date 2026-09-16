#!/usr/bin/env bash
# 本机启动 MCP app（macOS / Linux）。Windows 用仓库根的 start-local.bat。
#
# 为什么要这个脚本，而不是直接 `uvicorn app.main:app`：
# 本机拓扑是 **app(8001) 与 AS(8002) 分端口**，而 settings.py 里若干默认值是按
# "单端口、AS 挂在 app 自己身上" 写的。配置缺项时服务**照常启动**，但所有外部
# 令牌会在运行期被判 `malformed` —— 症状是"外部 Agent 全部 401"，日志里只有
# `mcp_token_rejected reason=malformed`，分不清是签名、issuer 还是 audience 出的错。
# 2026-09-16 的真实事故就是这样发生的，排查代价很高。
#
# 所以这里做**启动前自检并拒绝启动**。被拦下时先看这几项，不要去看令牌。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PORT="${APP_PORT:-8001}"
LOG_FILE="${LOG_FILE:-/tmp/hrbpilot-app-${PORT}.log}"

ENV_FILE="$REPO_ROOT/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "拒绝启动：找不到 $ENV_FILE" >&2
    echo "可从 env.docker.example 复制一份再按本机拓扑改（不要照抄 Docker 的地址）。" >&2
    exit 1
fi

# ── 1. 必需配置自检 ───────────────────────────────────────────────────────────
# 四项都必须是"填了且非空"。留空等同于没填 —— 这是事故里最容易踩的形态：
# 变量在、值是空的，看起来像配过了。
REQUIRED_KEYS=(
    EMBEDDING_BASE_URL          # 缺 → 向量化不可用 → dense 检索腿静默失效
    OAUTH_ISSUER                # 缺 → RS 去错误端口取元数据，iss 比对必失败
    PUBLIC_BASE_URL             # 缺 → aud 与客户端申请值不匹配，令牌全拒
    MCP_AUTHORIZATION_SERVERS   # 缺 → 不信任任何 AS，AS 令牌被退回平台校验
)

missing=()
for key in "${REQUIRED_KEYS[@]}"; do
    if ! grep -qE "^${key}=[^[:space:]]" "$ENV_FILE"; then
        missing+=("$key")
    fi
done

if [ ${#missing[@]} -gt 0 ]; then
    echo "拒绝启动：.env 缺少或留空了以下配置：" >&2
    printf '  - %s\n' "${missing[@]}" >&2
    cat >&2 <<'HINT'

  本机拓扑（app 8001 / AS 8002）需要：
    EMBEDDING_BASE_URL=https://ai.gitee.com/v1
    OAUTH_ISSUER=http://localhost:8002
    PUBLIC_BASE_URL=http://localhost:8001        # 决定 MCP 的 audience
    MCP_AUTHORIZATION_SERVERS=http://localhost:8002

  不配置的后果不是"启动失败"，而是启动后**所有外部令牌被判 malformed**、
  日志只有 reason=malformed（签名/issuer/audience 三种原因揉成一个值）。
  详见 docs/ops/2026-09-16-检索静默降级为稀疏单腿-定位与修复.md
HINT
    exit 1
fi

# ── 2. 清除继承来的临时代理 ───────────────────────────────────────────────────
# 沙箱 / Agent 环境会注入 HTTP(S)_PROXY，指向一个**每次调用临时生成**的本地代理：
# 端口随调用变化（实测先后见到 51041、65448），且**调用结束即消失**。
# 服务若继承它，会呈现一种极难查的形态 —— 启动那一刻能出网，几分钟后开始报
# `APIConnectionError: Connection error.`，dense 检索腿随之静默失效，
# 而 Postgres/Redis/Milvus 走 localhost 不受影响，于是 `/api/ready` 全绿。
# 本机实测直连可出网（不走代理返回 200），故一律清除，不继承调用方的代理。
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy NO_PROXY no_proxy

# ── 3. 出网可达性自检（不花 token，也不碰向量化接口的业务语义）────────────────
# 上一节那个坑的教训：**配置存在 ≠ 能力可用**。这里做的是真正的连通性检查 ——
# 只看"能不能建立连接"，不要求对方返回正常状态码（哪怕 404/502 也算可达，
# 因为那说明链路是通的）。curl 退出码非 0 / http_code=000 才判定不可达。
# 注意**不要**拿 /v1/models 判健康：本机实测它在 embedding 完全可用时也会返回 502。
PROBE_HOST="$(printf '%s' "$(grep -E '^EMBEDDING_BASE_URL=' "$ENV_FILE" | head -1 | cut -d= -f2-)" \
    | sed -E 's#^(https?://[^/]+).*#\1#')"
if [ -n "$PROBE_HOST" ]; then
    probe_code="$(curl -sS -o /dev/null -m 8 -w '%{http_code}' "$PROBE_HOST" 2>/dev/null || true)"
    if [ -z "$probe_code" ] || [ "$probe_code" = "000" ]; then
        # 注意：凡是 ${VAR} 后面紧跟中文/全角符号，必须用花括号 —— 写成 $VAR（
        # 时 bash 会把多字节字符的首字节并进变量名，配合 set -u 直接报
        # "unbound variable"。这个坑在中文脚本里出现率很高。
        echo "拒绝启动：连不上向量化端点 ${PROBE_HOST}（这会导致 dense 检索腿静默失效）。" >&2
        echo "  先在当前终端手工确认：curl -sS -o /dev/null -w '%{http_code}\\n' ${PROBE_HOST}" >&2
        echo "  若输出 000，多为代理/网络问题；注意 HTTP(S)_PROXY 常是临时注入的。" >&2
        exit 1
    fi
    echo "链路自检：${PROBE_HOST} 可达（HTTP ${probe_code}）"
fi

# ── 4. 端口占用自检 ───────────────────────────────────────────────────────────
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "拒绝启动：端口 $PORT 已被占用（先停掉旧进程再启动）：" >&2
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >&2
    exit 1
fi

# ── 5. 启动 ──────────────────────────────────────────────────────────────────
echo "启动 app: http://127.0.0.1:${PORT}  （日志 ${LOG_FILE}）"
exec ./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
