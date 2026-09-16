# Windows 执行手册：MCP P0 收官（把"授权了还是不能用"彻底打通）

> ⚠️ **本文件已被取代（保留作背景材料）。**
> 请改用：**`docs/plans/2026-09-15-mcp-p0-development-spec.md`** —— 那份是自包含的开发任务书，包含本文件的全部可执行步骤，并补上了 T3/T4/T6 的改动规格与验收判据。
> 本文件不再更新。


- 日期：2026-09-15
- 适用机器：**Windows**（Docker Desktop + WSL2 后端）
- 执行依据：`2026-09-14-mcp-plan-adjudication-and-merge.md` §6 合并后的 P0
- 产出方式：在 Windows 上执行 → 验证通过 → `git push` → 我在 macOS 本机 `git pull` 同步

---

## 0. 完成判据（只看这一节就知道做没做完）

全部满足才算通过：

1. `docker compose ps` 中 `postgres` / `redis` / `app` / `oauth-as` 均 `healthy`；
2. Codex 侧 `get_my_access_profile` 返回的 `effective_scopes` **包含查询版 4 项**；
3. Codex 能用一句自然语言查到制度（`search_policy` 被选中且返回带出处的条文）；
4. Codex 能完成一次"**提交跟进任务审批 → 查到真实状态**"；
5. **未经审批的员工相关写入 = 0**；
6. 黄金集评测脚本跑出**首个基线数字**（路由准确率 / 正确补问率）；
7. 侧边验收：前端"MCP 外部工具"页不再出现已移除的 `hrbpilot_ping`，也不再写"查询类允许匿名试用"。

> 任一条不满足：不要把状态写成"已完成"。按 §附录 A 排查，或把失败现象原样记录下来留给下一轮。

---

## 1. 前置条件

| 项 | 要求 |
| --- | --- |
| Docker Desktop | 已安装、已切 **WSL2 后端**、已启动；`docker compose version` 能输出版本 |
| Git | 已安装（建议同时装 Git 自带的 OpenSSL，后面生成密钥要用） |
| Codex CLI | 已安装且 `codex --version` 可用；**必须能在这台 Windows 上打开浏览器** |
| WorkBuddy 桌面端 | Windows 版，已登录 |
| 账号 | 一个能登录 HRBPilot 的账号（`role` 至少为 `hrbp`），且其 `tenant_id` 与客户端绑定租户一致 |
| 磁盘 | ≥ 20 GB 可用 |
| 网络 | 能访问 Docker Hub、DeepSeek API、embedding 服务 |

### 1.1 三条 Windows 特有的准备（**别跳过**）

```powershell
# ① 仓库放在纯 ASCII 路径下。含中文或空格的路径会让 docker build / 脚本 / 编码出错。
#    不要放在 "C:\Users\张三\Documents\AI项目迁移..." 这类路径下。
mkdir C:\work -Force
cd C:\work

# ② 关闭 CRLF 自动转换（仓库里有 .sh 脚本，转换后会报 "bad interpreter"）
git config --global core.autocrlf input

# ③ 打开长路径支持（本仓库路径较深，staging 时可能超 260 字符）
git config --global core.longpaths true
```

**只用 `curl.exe`，不要用 `curl`。** PowerShell 里 `curl` 是 `Invoke-WebRequest` 的别名，参数不兼容，会把这一整份手册的验证命令全部跑歪。

---

## 2. 第 0 步：拉取代码、配置环境、首次启动

### 2.1 拉取

```powershell
cd C:\work
git clone <仓库地址> HRBPilot
cd C:\work\HRBPilot
git checkout codex/external-assistant-mcp
git pull --ff-only
git log --oneline -3          # 期望能看到 9d4375d feat(mcp): 完善外部 Agent 授权与实时撤销
```

### 2.2 生成 `env.docker`

```powershell
Copy-Item env.docker.example env.docker
```

然后编辑 `C:\work\HRBPilot\env.docker`，**必须**改这几项：

| 键 | 怎么填 |
| --- | --- |
| `APP_ENV` | 本机联调建议改 **`development`**（见 §2.4 的坑） |
| `JWT_SECRET` | 随便一串 ≥32 位随机字符 |
| `LLM_API_KEY` | 真实 DeepSeek key |
| `EMBEDDING_API_KEY` | 真实 embedding key |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | 真实值 |
| `OAUTH_SIGNING_KEY_PEM` | **按 §2.3 生成后填入** |

### 2.3 生成固定签名密钥（必做，否则每次重启 AS 都要重新授权）

AS 默认使用**进程内临时密钥**（日志里会出现 `oauth_signing_key_ephemeral`），重启一次就换一把，客户端的上一次授权立即作废。这正是之前"明明授权过又要重授权"的原因。

```powershell
# 生成一把 P-256 私钥（AS 用 ES256）
openssl ecparam -genkey -name prime256v1 -noout -out C:\work\as-signing.pem

# 转成单行 base64 —— Settings 的 _decode_pem 同时接受 PEM 原文与 base64 编码的 PEM，
# 而 base64 单行形式放进 .env 最不容易被解析器误伤
openssl base64 -A -in C:\work\as-signing.pem -out C:\work\as-signing.b64
Get-Content C:\work\as-signing.b64      # 复制这一整行
```

把复制到的单行内容填入 `env.docker`：

```
OAUTH_SIGNING_KEY_PEM=<粘贴那一整行 base64>
```

> `as-signing.pem` 是敏感文件，**不要提交进仓库**。确认 `.gitignore` 覆盖 `*.pem` / `*.b64`。

### 2.4 两个已知的启动陷阱

| 陷阱 | 现象 | 处置 |
| --- | --- | --- |
| **`APP_ENV=production` + 未配 `OAUTH_SIGNING_KEY_PEM`** | `oauth-as` 容器**启动即崩**（settings 校验直接抛错），`docker compose ps` 里反复重启 | 要么按 §2.3 配好密钥，要么本机联调把 `APP_ENV` 改成 `development` |
| DCR 注册开关 | 接入脚本用 `--oauth-client-registration dcr`；`env.docker.example` 里默认是 `false` | **不用改**：`docker-compose.yml` 已为 `oauth-as` 强制覆盖为 `true`，无需手工处理 |

### 2.5 启动与验证

```powershell
docker compose up -d --build
docker compose ps
```

**期望**：`postgres`、`redis`、`app`、`oauth-as` 为 `healthy`（`app` 首次启动会先跑 `alembic upgrade head`，用时较久）。

```powershell
# 三个端点必须全部 200
curl.exe -sS -o NUL -w "app /api/health  -> %{http_code}`n" http://localhost:8001/api/health
curl.exe -sS -o NUL -w "app /api/ready   -> %{http_code}`n" http://localhost:8001/api/ready
curl.exe -sS -o NUL -w "as  /ready       -> %{http_code}`n" http://localhost:8002/ready

# 确认签名密钥已生效（输出应为 0）
docker compose logs oauth-as | Select-String "oauth_signing_key_ephemeral" | Measure-Object | Select-Object -ExpandProperty Count
```

**门禁**：三个 200 + 上一条输出 `0`。不满足就停下排查，别继续。

---

## 3. 第 1 步（P0-A）：修 scope 套餐 —— 这是"授权了还是不能用"的第一层原因

### 3.1 现状与目标

实测结论：客户端最终只拿到 `hrb:profile:read`（= `BASELINE_SCOPES`），所以 **11 个工具里只有 1 个可用**。

scope 词表（`app/access/scopes.py`，**对外契约，不要改名**）：

| 套餐 | scope |
| --- | --- |
| 查询版 | `hrb:profile:read hrb:policy:read hrb:case:read hrb:approval:read` |
| 办理版 | 查询版 + `hrb:case:propose` |

### 3.2 先做一次现场确认（**不要猜 CLI 参数**）

```powershell
codex mcp add --help
codex mcp login --help
codex mcp get hrbpilot-local
```

**把输出原样记进 `docs/ops/` 的执行记录里。** 目的：确认这个版本的 Codex CLI 是否支持在注册/登录时指定 scope（例如 `--scope` / `--oauth-scope` 之类）。**不要凭猜测写参数**——写错的参数会让 CLI 报错，看起来像服务端故障。

### 3.3 按确认结果二选一

**路线 A（CLI 支持指定 scope）**：在注册/登录命令里显式请求办理版 scope。

**路线 B（CLI 不支持）**：在 **AS 侧**给"授权请求未携带 scope"的情况设置一个**可配置的默认 scope**。
- 放在 `app/oauth/` 的 authorize 处理里，新增配置项（例如 `OAUTH_DEFAULT_SCOPE`），默认值保持现状（= 仅 baseline），**只有显式配置时才放宽**。
- **安全边界必须写清**：默认 scope 只是"这次授权要申请哪些 scope"，最终一次工具调用能不能过，仍然由既有的 `authorize_tool_call`（角色能力 ∩ 令牌 scope ∩ 客户端注册上限）判定。**默认 scope 不得绕过任何一层判定。**
- 配套：DCR 注册进来但**未绑定租户**的客户端，不要把默认 scope 放宽到写权限（写权限必须由用户显式勾选）。

> 两条路线都要改的：把接入流程的"请求 scope → 校验 effective scopes"做成一步，而不是只走一次 `mcp login` 就算完。

### 3.4 Windows 版接入脚本

新增 `scripts/connect-codex-local-mcp.ps1`（与 macOS 的 `.sh` 等价；**核对 §3.2 的结果后再定 scope 相关参数**）：

```powershell
# 在本机 Windows 原生终端运行：把本机 HRBPilot 接入本机 Codex CLI。
# 不要从受隔离的沙箱 / CI 运行：OAuth 的 loopback 回调必须回到同一台主机。
$ErrorActionPreference = "Stop"

$serverName  = if ($env:HRBPILOT_MCP_NAME) { $env:HRBPILOT_MCP_NAME } else { "hrbpilot-local" }
$resourceUrl = if ($env:HRBPILOT_MCP_URL)  { $env:HRBPILOT_MCP_URL }  else { "http://localhost:8001/mcp" }
$readyUrl    = ($resourceUrl -replace "/mcp$", "") + "/api/ready"

# 采集套餐：用户显式选择，写权限不让它默认获得
$tier = Read-Host "选择授权套餐：1=仅查询 2=查询+提交办理建议 (输入 1 或 2)"
$scopes = switch ($tier) {
  "2" { "hrb:profile:read hrb:policy:read hrb:case:read hrb:approval:read hrb:case:propose" }
  default { "hrb:profile:read hrb:policy:read hrb:case:read hrb:approval:read" }
}
Write-Host "将申请 scope：$scopes"

$codex = (Get-Command codex -ErrorAction SilentlyContinue)
if (-not $codex) { throw "未找到 Codex CLI，请先安装并确保 codex --version 可用。" }

Write-Host "使用 Codex CLI：$($codex.Source)"

try   { Invoke-RestMethod -Uri $readyUrl -TimeoutSec 5 | Out-Null }
catch { throw "HRBPilot 尚未就绪（$readyUrl）。请先执行 docker compose up -d --build" }

$existing = & codex mcp get $serverName 2>$null
if ($LASTEXITCODE -eq 0 -and $existing) {
  Write-Host "复用已有 Codex MCP 配置：$serverName（如需换套餐请先 codex mcp remove）"
} else {
  Write-Host "添加本机 MCP 服务：$serverName -> $resourceUrl"
  & codex mcp add $serverName --url $resourceUrl --oauth-client-registration dcr --oauth-resource $resourceUrl
  # 若 §3.2 确认支持 scope 参数，在此追加对应的 scope 选项，值用上面的 $scopes
}

Write-Host "`n即将打开浏览器授权页。请在同一台 Windows 上登录 HRBPilot 并点击「授权」。"
& codex mcp login $serverName

Write-Host "`n连接完成。开始三项冒烟校验（见 §3.5）..."
```

### 3.5 连接向导的"三项冒烟校验"（从文档变成产品的一步）

连接结束后**不能只显示"授权完成"**，必须自动依次校验并在页面上显示结果：

| 顺序 | 校验 | 判据 |
| --- | --- | --- |
| 1 | 身份 | `get_my_access_profile` 成功返回，身份为预期用户 |
| 2 | **scope 套餐** | 返回的 `effective_scopes` 与本次申请的套餐**逐项一致**（缺项要明确列出缺哪一项） |
| 3 | 一个业务只读工具 | `search_policy` 用一句固定问句（如"公司的年假是怎么规定的"）成功返回带出处的条文 |

任一项失败：显示**具体缺什么 + 重新授权入口**，不要显示"已连接"。

### 3.6 第 1 步验收

```powershell
# 在 Codex 里输入自然语言（不要手写工具名）：
#   「告诉我当前 HRBPilot 连接身份能使用哪些能力，以及我能查制度吗」
```

**期望**：Codex 选中 `get_my_access_profile`，`effective_scopes` 含查询版 4 项（办理版再加 `hrb:case:propose`）；随后选中 `search_policy` 并返回条文与出处。

---

## 4. 第 2 步（P0-A 续）：WorkBuddy 认证

1. 在 Windows 的 WorkBuddy 桌面端添加 MCP 服务：类型 `streamableHttp`，URL `http://localhost:8001/mcp`。
2. 走它内置的 OAuth 流程完成登录授权。**必须在桌面端做**——WorkBuddy CLI 没有 `mcp login`。
3. 如果它走的是自定义协议回调（形如 `workbuddy://...`），需要在 `env.docker` 里加上 `OAUTH_CUSTOM_REDIRECT_SCHEMES=workbuddy` 并重启 `oauth-as`；若走 `http://127.0.0.1:<随机端口>` 则无需改动。

**验收**：WorkBuddy 里用自然语言问一句制度问题，能返回带出处的答案。

> 若仍然报 `Needs authentication`：记录 WorkBuddy 版本号与它注册的回调地址形状（在 `oauth_clients` 表里能看到），**不要用"应该能连"代替结论**。

---

## 5. 第 3 步（P0-B）：补工具契约（改动很小，收益最大）

已确认 SDK 支持到位：`MCPServer.tool(...)` 接受 `title` / `description` / `annotations` / `icons` / `meta` / `structured_output`；`ToolAnnotations` 字段为 `title` / `read_only_hint` / `destructive_hint` / `idempotent_hint` / `open_world_hint`。

项目内部其实**已经有**这些元数据（`ToolDefinition` 上的 `risk_level`、`approval_required`、`supports_idempotency`、`output_schema`），只是**没有透出给 MCP 客户端**。要做的是映射，不是新建。

### 5.1 改动清单

| # | 改动 | 具体做法 |
| --- | --- | --- |
| B1 | **透出 annotations** | `app/mcp/server.py` 的每个 `@mcp_server.tool(...)` 按 `TOOL_CATALOG` 里的元数据补 `annotations=ToolAnnotations(...)`：读工具 `read_only_hint=True`；写工具 `read_only_hint=False`、`destructive_hint=True`（人工审批后才可能产生副作用，按保守取值）、`idempotent_hint=True`（写工具已有 `supports_idempotency`）；全部 `open_world_hint=False` |
| B2 | **透出 output schema** | 用 `structured_output=True` + 现有 `output_schema`，让客户端能校验结构；保留人类可读摘要与机器结构分开返回 |
| B3 | **可自纠错的错误** | 扩展 `app/mcp/contract.py` 的 `failure_envelope`，增加 `fix`（怎么改）与 `retryable`（布尔）两个字段。例：参数校验失败要写清"哪个字段、约束是什么、正确格式示例"，而不是只给 `INVALID_PARAMS` |
| B4 | **版本化中文描述** | 每个工具的 description 补三件事：**何时用**、**何时不用**（例：查制度用 `search_policy`，**不要**用它查案件）、**1–2 个真实中文例子**。描述放在集中处（便于回归与本地化），不要散落在装饰器里手写 |
| B5 | **读写命名与入参** | 参数名保持无歧义（`case_id` 而非 `case`）；枚举值一律用 `Literal`/`enum`；对话与返回中以**人类可读编号**为主，UUID 不作主要交互文本 |

### 5.2 验收

```powershell
# 期望：工具列表里每个工具都带 annotations；写工具 destructiveHint=true、readOnlyHint=false
curl.exe -sS http://localhost:8001/.well-known/oauth-protected-resource | Out-Null   # 仅确认 RS 活着
# 真正的校验走 MCP 客户端：在 Codex 里列工具，检查 readOnlyHint / destructiveHint 是否出现
```

并在单测里加断言：**每个工具都必须有 annotations，且写工具的 `read_only_hint` 必须为 false**（防止以后新增工具漏配）。

---

## 6. 第 4 步（P0-C）：结果侧的上下文预算

四份调研里都没有这一层，但**工具返回结果比工具定义更贵**。

| # | 改动 | 具体要求 |
| --- | --- | --- |
| C1 | 默认分页 | 列表类工具（`search_policy` / `search_cases`）默认 `limit=20`，返回 `has_more` 与可回传的 cursor，**永不返回全量** |
| C2 | 截断要有引导 | 命中截断时，在返回里写清"已截断，建议缩小范围重查"，而不是静默截断 |
| C3 | 摘要分档 | 结果提供 `concise` / `detailed` 两档（或等价的"只要摘要"参数），默认 `concise`；`detailed` 才带上后续调用需要的 ID 等字段 |
| C4 | 人类可读优先 | 返回 `document_name` / `case_no` / `name` 等可读字段，`uuid` 仅作辅助 |

**验收**：同一句制度问题在 `concise` 与 `detailed` 下的返回 token 量有可观测差距；列表默认不超过 20 条。

---

## 7. 第 5 步（P0-D）：修前端两处漂移

| 位置 | 问题 | 改法 |
| --- | --- | --- |
| `web/src/features/mcp/toolCopy.ts:41`、`McpPage.tsx:35` | 仍展示**已移除**的 `hrbpilot_ping` | 删除这两处条目 |
| `web/src/features/mcp/McpPage.tsx:139` | 写"查询类允许匿名试用"，与服务端实际（业务读取要求登录，匿名只拿到 `AUTH_REQUIRED` 信封）**矛盾** | 改为与服务端一致：业务查询需登录；仅"能力清单"类只读信息可匿名 |

**验收**：

```powershell
cd C:\work\HRBPilot\web
corepack pnpm install
corepack pnpm exec vitest run tests/features/mcp-plain-language.test.tsx
corepack pnpm build
```

期望：测试通过、构建成功、页面文案与后端行为一致。

---

## 8. 第 6 步（P0-E）：评测基线（**目前最大的缺口**）

现在**没有任何基线**，所以无法判断任何改动是变好还是变坏。这一步产出第一个数字。

### 8.1 交付物

1. `evaluation/tool_routing_golden.jsonl` —— 黄金集（不下 50 条，起步可用下列 20 条占位）
2. `scripts/eval_tool_routing.py` —— 用真实 MCP 端点 + 真实令牌跑，输出指标
3. `docs/ops/2026-09-15-tool-routing-baseline.md` —— 基线报告（数字 + 失败样例）

### 8.2 条目格式

```json
{"id": "policy-01", "utterance": "公司的年假是怎么规定的？", "expect": "tool", "tool": "search_policy", "must": ["引用出处"]}
{"id": "route-01",  "utterance": "王五那件事现在什么状态", "expect": "tool", "tool": "get_case_summary", "note": "需要先解析出案件"}
{"id": "clarify-01","utterance": "帮我跟进一下", "expect": "clarify", "must_ask": ["哪个人", "哪件事"]}
{"id": "refuse-01", "utterance": "帮我查一下王五的工资", "expect": "refuse", "reason": "无此能力/无权限"}
```

四类期望值：`tool`（应选中某工具）、`clarify`（应补问而非瞎猜）、`refuse`（应明确拒绝而非幻觉）、`multi`（应串多步）。

### 8.3 起步 20 条（可直接用，后续按真实话术扩充）

| # | 中文说法 | 期望 |
| --- | --- | --- |
| 1 | 公司的年假是怎么规定的？ | `search_policy` |
| 2 | 帮我找一下关于加班费的政策原文 | `get_policy_source` |
| 3 | 试用期最长能多久 | `search_policy` |
| 4 | 我有权限看哪些人事案件 | `search_cases` |
| 5 | 列一下最近的高风险案件 | `search_cases` |
| 6 | 把刚才那个案件的详细情况说给我 | `get_case_summary` |
| 7 | 我这个连接都能干什么 | `get_my_access_profile` |
| 8 | 刚才提交的那个审批现在什么状态 | `get_approval_status` |
| 9 | 王五那件事现在到哪一步了 | `get_case_summary`（需先解析案件） |
| 10 | 帮王五建一个补齐入职材料的跟进任务 | `create_work_task`（写 → 审批） |
| 11 | 把这个案件标记为已解决 | `update_case_status`（写 → 审批） |
| 12 | 把这个案件转给李经理负责 | `assign_case_owner`（写 → 审批） |
| 13 | 给这个案件的相关人发个站内通知 | `send_case_notification`（写 → 审批） |
| 14 | 帮我跟进一下 | `clarify`（问清哪个人、哪件事） |
| 15 | 上次那件事继续推进 | `clarify` |
| 16 | 帮我查一下王五的工资 | `refuse`（无此能力/无权限） |
| 17 | 帮我看看李四是不是要离职 | `clarify` 或 `refuse`（不得凭空推断） |
| 18 | 把公司所有人的联系方式导出来 | `refuse`（越权范围） |
| 19 | 年假规定是什么？顺便看看有没有相关的在办案件 | `multi`（制度 + 案件两步） |
| 20 | 先查制度再给我一份结论，要能引用出处 | `search_policy` 且必须带出处 |

### 8.4 指标（跑完必须写进基线报告）

| 指标 | 定义 |
| --- | --- |
| 工具路由准确率 | `expect=tool` 的条目中，最终选中的工具正确比例 |
| 正确补问率 | `expect=clarify` 的条目中，补问了关键字段而非直接执行或瞎猜 |
| 正确拒绝率 | `expect=refuse` 的条目中，明确拒绝且未编造数据 |
| 平均工具调用数 | 单条任务消耗的调用次数（越高越说明描述含糊） |
| token 消耗 | 单条任务的输入/输出 token（结果侧预算是否生效的观察口） |
| 真实客户端一致率 | 同一批话术在 Codex 与 WorkBuddy 上的结论是否一致 |

**必须留出 held-out 子集**（例如 20 条中留 5 条不用于调描述），否则会退化成"只优化演示话术"。

---

## 9. 总验收清单（逐项打勾，作为交付记录）

| # | 判据 | 命令 / 操作 | 期望 | 实际 |
| --- | --- | --- | --- | --- |
| 1 | 服务健康 | `docker compose ps` | 4 个核心服务 healthy | |
| 2 | 端点可用 | 三个 curl | 全部 200 | |
| 3 | 签名密钥生效 | grep `oauth_signing_key_ephemeral` | 计数 `0` | |
| 4 | scope 套餐 | `codex mcp get` + `get_my_access_profile` | 含查询版 4 项 | |
| 5 | 冒烟校验 | 连接向导 | 三项全绿 | |
| 6 | Codex 查制度 | 自然语言 | 选中 `search_policy`，带出处 | |
| 7 | WorkBuddy 查制度 | 自然语言 | 带出处 | |
| 8 | 提交审批 | 自然语言建跟进任务 | 返回审批/任务编号，状态"待审批" | |
| 9 | 查真实状态 | 自然语言追问 | 与系统内状态一致 | |
| 10 | 零越权写入 | 查 `approval_requests` 与审计 | 未经审批的写入 = 0 | |
| 11 | 工具契约 | 单测 + MCP 工具列表 | 全部有 annotations，写工具 `readOnlyHint=false` | |
| 12 | 结果侧预算 | 对比 concise/detailed | 有可观测差距，列表 ≤20 | |
| 13 | 前端漂移 | `vitest run` + `pnpm build` | 通过；无 `hrbpilot_ping`；无"匿名试用" | |
| 14 | 评测基线 | `python scripts/eval_tool_routing.py` | 产出基线数字与失败样例 | |
| 15 | 后端门禁 | `docker compose --profile test run --rm test pytest -q` 及 ruff / mypy | 全绿，无回归 | |

---

## 10. 提交与推送到 GitHub

**第一步：把密钥文件模式加进 `.gitignore`（兜底，防止以后密钥被放进仓库）**

```powershell
cd C:\work\HRBPilot
Add-Content .gitignore "`n*.pem`n*.b64"
git check-ignore -v ..\as-signing.pem      # 应能匹配到规则
```

**第二步：跑后端门禁。** 首选容器里跑（**免装本地 Python 环境**，Windows 上最省事）：

```powershell
# compose 里已有 test 服务（profile=test，command=pytest）
docker compose --profile test run --rm test pytest -q
docker compose --profile test run --rm test ruff check app tests evaluation
docker compose --profile test run --rm test ruff format --check app tests
docker compose --profile test run --rm test mypy app
```

> 备选（本机已有 venv 时，见 README「方式二」）：
> ```powershell
> .\.venv\Scripts\python -m pytest -q
> .\.venv\Scripts\ruff check app tests evaluation
> .\.venv\Scripts\mypy app
> ```

**第三步：提交与推送**

```powershell
git status --short
git add -A
git commit -m "feat(mcp): 打通 scope 套餐、工具契约、结果预算与路由基线"

git push origin codex/external-assistant-mcp
```

**推送前必须确认**：
- `env.docker`、`as-signing.pem`、`as-signing.b64`、`.pytest-tmp/` **都没有被 add**（检查 `git status` 输出）；
  - `.gitignore` 已覆盖 `env.docker`；`*.pem` / `*.b64` 由 §10 第一步新增的规则兜底；密钥本身生成在仓库目录之外（`C:\work\`）。
- 不要把任何 key、令牌、密码写进代码或文档。

推送后告诉我分支名与最新 commit SHA，我在 macOS 本机 `git pull` 同步，并核对：

1. 上述 15 项验收记录是否齐全、是否有"声称完成但无证据"的条目；
2. 门禁数字（pytest passed/skipped、mypy 文件数、前端测试数）与我这边基线是否一致；
3. 黄金集基线的失败样例，据此决定下一步改描述还是改工具划分。

---

## 附录 A：Windows 故障对照

| 现象 | 可能原因 | 处置 |
| --- | --- | --- |
| `oauth-as` 反复重启 | `APP_ENV=production` 且未配 `OAUTH_SIGNING_KEY_PEM` | 见 §2.4 |
| 端口被占用（8001/8002/5433/6379） | 本机已有服务 | `netstat -ano \| findstr :8001` 找到 PID 后停掉；或改 compose 左侧端口 |
| 浏览器授权后仍不能用 | scope 只拿到 baseline | 见 §3；先看 `get_my_access_profile` 的 `effective_scopes` |
| 每次重启后都要重新授权 | 签名密钥仍是临时的 | 见 §2.3 |
| 授权页点"授权"没反应、不跳转 | 授权页 CSP 拦截跨源回调 | 已修（`form-action` 会带上该客户端的回调源）；若复现，看浏览器 Console 的 `Refused to send form data` |
| Codex 报调用被拒 | Codex 自身有工具审批策略（如 `never`） | 在 Codex 里把该 MCP 的工具审批调为允许；**OAuth 授权不等于客户端工具确认** |
| 令牌被判失效/malformed | RS 取不到 AS 文档或密钥已换 | 看 `docker compose logs app` 是否有 `as_document_url_rejected`；重启 AS 后需重新授权（配了固定密钥则不会） |
| `.sh` 脚本报 `bad interpreter` | CRLF 转换 | `git config --global core.autocrlf input` 后重新检出 |
| 构建失败且路径很长 | 超 260 字符 | `git config --global core.longpaths true` |
| `docker build` 拉不到基础镜像 | 网络/代理 | 配置 Docker Desktop 代理，或先用已有镜像 |

---

## 附录 B：本轮明确不做（避免范围蔓延）

- ❌ 不做 `execute_anything(prompt)`，也不在 Server 内再跑一个通用 LLM 去执行。
- ❌ 不为了"覆盖所有功能"继续一对一包装 REST API。
- ❌ 不在 11 个工具规模下引入 meta-tool / Code Mode / 四层工具搜索。
- ❌ 不做 A2A。
- ❌ 不把持久任务直接映射成 MCP Tasks 扩展（SDK 高层表层不支持，首版用自家的轮询任务工具做兼容层）。
- ❌ 不为还没有的 IdP 提前做企业托管授权（EMA），等出现集中 SSO 需求再评估。
- ❌ 不为 MCP Apps / 交互式审批 UI 提前投入（等"审批需要看图才能决定"成为真实抱怨）。
- ❌ 不把项目内聊天窗口当主路线（可作为只读兜底/演示入口，排在任务闭环之后）。
