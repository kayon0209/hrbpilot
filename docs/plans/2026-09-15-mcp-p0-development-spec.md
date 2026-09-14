# HRBPilot MCP P0 开发任务书

- 日期：2026-09-15
- 用途：**交给另一台电脑（Windows）独立实现**。本文件自包含，执行者不需要读其它文档。
- 分支：`codex/external-assistant-mcp`　基线：`9d4375d`（已推送）
- 目标一句话：**让 HR / HR 经理用自己的 AI Agent（Codex、WorkBuddy）用中文说完一句话，就能安全地查到制度、提交办理、并追踪到真实状态。**

---

## 给执行者的开场指令（可直接粘贴）

> 读取仓库根目录下 `docs/plans/2026-09-15-mcp-p0-development-spec.md`，按其中的 T1–T7 顺序实现。
> 每个任务完成后**必须**跑该任务的验收命令，并把实际输出记录到 `docs/ops/2026-09-15-execution-record.md`（新建）。
> 任一验收不通过就停下报告，不要绕过、不要用"应该没问题"代替结论。
> 任务 T2 第 2 步需要先做现场确认（不要猜 CLI 参数）。全部完成后按 T7 提交并推送分支 `codex/external-assistant-mcp`。

---

## 0. 完成判据（Definition of Done）

全部满足才算完成。任一条不满足，**不要**把状态写成"已完成"。

| # | 判据 | 怎么验 |
| --- | --- | --- |
| D1 | 四个核心容器 healthy（`postgres` / `redis` / `app` / `oauth-as`） | `docker compose ps` |
| D2 | AS 不再使用临时签名密钥 | `docker compose logs oauth-as` 中 `oauth_signing_key_ephemeral` 出现次数为 `0` |
| D3 | Codex 拿到的 `effective_scopes` 含查询版 4 项（办理版再加 1 项） | Codex 内自然语言触发 `get_my_access_profile` |
| D4 | Codex 与 WorkBuddy **各自**能用中文查到制度，且回答带出处 | 各问一句"公司的年假是怎么规定的？" |
| D5 | Codex 能完成"提交跟进任务审批 → 查到真实状态" | 两句自然语言，前后状态一致 |
| D6 | **未经审批的员工相关写入 = 0** | 查 `approval_requests` 与审计表 |
| D7 | 评测脚本产出**首个路由基线数字** | `docker compose --profile test run --rm test python scripts/eval_tool_routing.py` |
| D8 | 前端两处漂移已修 | `web` 单测 + `pnpm build` 通过 |
| D9 | 后端门禁全绿 | 见 T7 |

---

## 1. 必读的既有事实（避免重复踩坑）

### 1.1 项目现状（不要推翻的部分）

- MCP 出口挂在 `/mcp`（Streamable HTTP，**`stateless_http=True` 已开**）；另支持 stdio。
- **11 个工具 = 6 读 + 5 写**（以运行时的 `tools/list` 为准）：
  - 读：`search_policy`、`get_policy_source`、`search_cases`、`get_case_summary`、`get_approval_status`、`get_my_access_profile`
  - 写：`create_hr_case`、`assign_case_owner`、`send_case_notification`、`update_case_status`、`create_work_task`
- 写工具**只创建 `ApprovalRequest`**，永不直接执行业务写入；审批与调用审计**同事务**。
- **唯一判定链**：`app/mcp/auth/authorization.py` 的 `authorize_tool_call`（角色能力 ∩ 令牌 scope ∩ 客户端授权上限），`/mcp` 与 `/api/mcp` 两条出口共用。**本次任何改动都不允许让判定按出口分叉。**
- 授权服务器（AS）与资源服务器（RS）分离：AS `:8002`、RS(app) `:8001`；令牌由 AS 签发，RS 按 `tenant_id` 定位数据。

> **不要做的事**：不要把工具改成"任意 prompt 直接执行"；不要让 LLM 直连数据库或 HTTP；不要绕过审批；不要在 Server 内再跑一个通用 LLM。以上都是既有设计红线。

### 1.2 技术世代与能力边界（已实测）

| 事实 | 值 |
| --- | --- |
| Python MCP SDK | `mcp 2.2.0` |
| 协议世代 | `LATEST_PROTOCOL_VERSION = 2026-07-28`（最新） |
| 高层 `MCPServer` 表层**支持** | `tool` / `resource` / `prompt` / `completion` / `icons` / `instructions` / `middleware` / `custom_route` / `stateless` / **`annotations`** / **`structured_output`** |
| 高层 `MCPServer` 表层**不支持** | `tasks` / `server/discover` / `elicitation`（源码 0 命中）→ 做 Tasks 需下沉到低层 `Server`，**本轮不做** |
| 已废弃（规范层） | `sampling` / `roots` / `logging` —— **不要往这些方向投入** |
| `MCPServer.tool(...)` 可传参数 | `name` / `title` / `description` / `annotations` / `icons` / `meta` / `structured_output` |
| `ToolAnnotations` 字段 | `title` / `read_only_hint` / `destructive_hint` / `idempotent_hint` / `open_world_hint` |

### 1.3 端口与端点（实测均 200）

| 服务 | 端口 | 验证 |
| --- | --- | --- |
| app（RS） | 8001 | `/api/health`、`/api/ready` |
| oauth-as | 8002 | `/ready` |
| web | 3001 | — |
| postgres | 5433 | — |
| redis | 6379 | — |
| minio | 9000 / 9001 | — |
| milvus | 19531 | — |

### 1.4 scope 词表（**对外契约，禁止改名**）

来源 `app/access/scopes.py`：

| 套餐 | scope 串 |
| --- | --- |
| 基线（任何已认证主体） | `hrb:profile:read` |
| **查询版** | `hrb:profile:read hrb:policy:read hrb:case:read hrb:approval:read` |
| **办理版** | 查询版 + `hrb:case:propose` |

---

## 2. T1 环境准备与首次启动

### 步骤

```powershell
# 2.1 仓库必须放在纯 ASCII 路径下（含中文或空格的路径会导致构建/脚本/编码出错）
mkdir C:\work -Force
cd C:\work
git clone <仓库地址> HRBPilot
cd C:\work\HRBPilot
git checkout codex/external-assistant-mcp
git pull --ff-only
git log --oneline -3            # 期望可见 9d4375d feat(mcp): 完善外部 Agent 授权与实时撤销

# 2.2 Windows 三条准备
git config --global core.autocrlf input     # 否则 .sh 会报 bad interpreter
git config --global core.longpaths true     # 否则深路径 staging 失败
# 另外：本任务书所有命令中的 curl 一律用 curl.exe（PowerShell 的 curl 是 Invoke-WebRequest 别名）

# 2.3 忽略规则兜底（.gitignore 目前不含这两条）
Add-Content .gitignore "`n*.pem`n*.b64"

# 2.4 生成固定签名密钥（必做）
openssl ecparam -genkey -name prime256v1 -noout -out C:\work\as-signing.pem
openssl base64 -A -in C:\work\as-signing.pem -out C:\work\as-signing.b64
Get-Content C:\work\as-signing.b64      # 复制这一整行，稍后填入 env.docker
```

### 关键：`env.docker` 的两个陷阱

```powershell
Copy-Item env.docker.example env.docker
```

| 陷阱 | 说明 | 处置 |
| --- | --- | --- |
| **签名密钥** | `app/oauth/keys.py` 在 `settings.is_production` 且 `OAUTH_SIGNING_KEY_PEM` 为空时**直接抛错**，`oauth-as` 启动即崩；而 `env.docker.example` 默认 `APP_ENV=production` | 按 2.4 生成后填入；或本机联调先把 `APP_ENV` 改成 `development` |
| **密钥格式** | `_decode_pem` **接受 PEM 原文或 base64 编码的 PEM**；AS 用 **ES256**，密钥必须是 **EC P-256** | 用 2.4 的**单行 base64** 最稳 |

`env.docker` 必改项：`APP_ENV`（建议 `development`）、`JWT_SECRET`（≥32 位）、`LLM_API_KEY`、`EMBEDDING_API_KEY`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`、`OAUTH_SIGNING_KEY_PEM`。

> **不需要改**：`OAUTH_ENABLE_DYNAMIC_REGISTRATION` —— `env.docker.example` 里是 `false`，但 `docker-compose.yml` 已为 `oauth-as` 强制覆盖为 `true`，接入脚本用的 DCR 注册可用。

### 启动与验收

```powershell
docker compose up -d --build
docker compose ps

curl.exe -sS -o NUL -w "app /api/health -> %{http_code}`n" http://localhost:8001/api/health
curl.exe -sS -o NUL -w "app /api/ready  -> %{http_code}`n" http://localhost:8001/api/ready
curl.exe -sS -o NUL -w "as  /ready      -> %{http_code}`n" http://localhost:8002/ready

# 期望输出 0
docker compose logs oauth-as | Select-String "oauth_signing_key_ephemeral" | Measure-Object | Select-Object -ExpandProperty Count
```

**T1 验收**：四个核心容器 healthy + 三个端点 200 + 上一条输出 `0`。**不满足就停下排查**（见 §10）。

---

## 3. T2 scope 套餐与连接冒烟（**本次最高优先级**）

### 问题陈述（已实测）

客户端最终只拿到 `hrb:profile:read`（= 基线 scope），导致 **11 个工具里只有 1 个可用**。这是"授权了还是不能用"的第一层原因。

### 步骤 1：现场确认 CLI 能力（**不要猜参数**）

```powershell
codex --version
codex mcp add --help
codex mcp login --help
codex mcp get hrbpilot-local
```

把输出**原样**记进 `docs/ops/2026-09-15-execution-record.md`。目的：确认该版本 Codex CLI 是否支持在注册/登录时指定 scope。**写错的参数会让人误判为服务端故障。**

### 步骤 2：按确认结果二选一

**路线 A —— CLI 支持指定 scope**：在注册/登录命令里显式请求套餐（值见 §1.4）。

**路线 B —— CLI 不支持**：在 **AS 侧**为"授权请求未携带 scope"的情况增加**可配置默认 scope**：
- 新增配置项（例如 `OAUTH_DEFAULT_SCOPE`，默认值保持现状 = 仅基线；**只有显式配置时才放宽**）。
- **必须写进注释的安全边界**：默认 scope 只决定"本次授权申请哪些权限"，最终一次工具调用能否通过仍由 `authorize_tool_call`（角色能力 ∩ 令牌 scope ∩ 客户端注册上限）判定。**默认 scope 不得绕过任何一层判定。**
- DCR 注册但**未绑定租户**的客户端，**不得**把默认 scope 放宽到 `hrb:case:propose`（写权限必须由用户在授权页显式选择）。

**两条路线都要做**：把"请求 scope → 校验 effective scopes"做成接入流程的一步，而不是只走一次 `codex mcp login` 就算完。

### 步骤 3：新增 Windows 接入脚本 `scripts/connect-codex-local-mcp.ps1`

与已有的 `scripts/connect-codex-local-mcp.sh` 等价。要点：

1. 读取套餐选择（`1=仅查询` / `2=查询+提交办理建议`），写权限**不默认授予**；
2. 用 `Invoke-RestMethod` 检查 `http://localhost:8001/api/ready`，不就绪则明确报错并提示先 `docker compose up -d --build`；
3. 已存在同名 MCP 配置时复用，否则执行 `codex mcp add <name> --url http://localhost:8001/mcp --oauth-client-registration dcr --oauth-resource http://localhost:8001/mcp`（**按步骤 1 的确认结果决定是否追加 scope 选项**）；
4. 调用 `codex mcp login <name>` 打开浏览器授权；
5. **结束后自动执行下方三项冒烟校验并打印结果**。

### 步骤 4：连接向导的"三项冒烟校验"（从文档变成产品的一步）

连接完成**不能只显示"授权完成"**，必须自动依次校验并展示：

| 顺序 | 校验 | 判据 | 失败时 |
| --- | --- | --- | --- |
| 1 | 身份 | `get_my_access_profile` 返回预期用户 | 报"身份解析失败" |
| 2 | **scope 套餐** | 返回的 `effective_scopes` 与申请套餐**逐项一致** | **明确指出缺哪一项** + 重新授权入口 |
| 3 | 一个业务只读工具 | `search_policy` 用固定问句成功返回带出处的条文 | 报具体错误码 |

### 步骤 5：WorkBuddy 认证

1. 在 **WorkBuddy Windows 桌面端**添加 MCP 服务：类型 `streamableHttp`，URL `http://localhost:8001/mcp`。
2. 走内置 OAuth 完成登录。**必须在桌面端做**——WorkBuddy CLI 没有 `mcp login`。
3. 若它使用自定义协议回调（形如 `workbuddy://...`），在 `env.docker` 加 `OAUTH_CUSTOM_REDIRECT_SCHEMES=workbuddy` 并重启 `oauth-as`；若回调是 `http://127.0.0.1:<随机端口>` 则无需改动。

### T2 验收

```powershell
# 在 Codex 中输入自然语言（不要手写工具名）：
#   「告诉我当前 HRBPilot 连接身份能使用哪些能力，以及我能不能查制度」
```

期望：选中 `get_my_access_profile`，`effective_scopes` 含查询版 4 项（办理版再加 `hrb:case:propose`）；随后选中 `search_policy` 并返回条文与出处。
若仍报 `Needs authentication`：记录 WorkBuddy 版本号与它注册的回调地址形状（可从 `oauth_clients` 表看到）。**不要用"应该能连"代替结论。**

---

## 4. T3 工具契约（改动很小，收益最大）

**关键认知**：项目内部**已经有**这些元数据（`app/scenarios/hr_case_agent/tools.py` 的 `ToolDefinition` 上有 `kind` / `risk_level` / `approval_required` / `supports_idempotency` / `output_schema`），只是**没有透出给 MCP 客户端**。所以这是**映射**，不是新建。

| # | 改动 | 要求 | 涉及文件 |
| --- | --- | --- | --- |
| T3-1 | 透出 `annotations` | 每个 `@mcp_server.tool(...)` 按 catalog 元数据补 `annotations=ToolAnnotations(...)`：读工具 `read_only_hint=True`；写工具 `read_only_hint=False`、`destructive_hint=True`（保守取值：真正的副作用在人工审批之后）、`idempotent_hint=True`；全部 `open_world_hint=False` | `app/mcp/server.py` |
| T3-2 | 透出结构化输出 | 启用 `structured_output=True` 并复用现有 `output_schema`；**人类可读摘要与机器结构分开返回** | `app/mcp/server.py` |
| T3-3 | 可自纠错的错误 | 扩展 `failure_envelope`，增加 `fix`（怎么改）与 `retryable`（布尔）。参数校验失败必须写清"哪个字段、约束是什么、正确格式示例"，不得只给 `INVALID_PARAMS` | `app/mcp/contract.py` |
| T3-4 | 版本化中文描述 | 每个工具的 description 必须含三件事：**何时用**、**何时不用**（例：查制度用 `search_policy`，**不要**用它查案件）、**1–2 个真实中文例子**。描述集中存放，便于回归与本地化，不要散落在装饰器里手写 | `app/mcp/server.py` + 描述文件 |
| T3-5 | 入参与枚举 | 参数名保持无歧义（`case_id` 而非 `case`）；固定取值用 `Literal`/`enum`；`additionalProperties` 收紧；对话与返回中以**人类可读编号**为主，UUID 仅作辅助 | `tools.py` / `contract.py` |

### T3 验收

1. 单测断言：**每个工具都必须有 annotations，且写工具的 `read_only_hint` 必须为 `False`**（防止以后新增工具漏配）。
2. 在 MCP 客户端列工具，确认 `readOnlyHint` / `destructiveHint` 出现且取值正确。
3. 构造一次参数错误，确认返回里含 `fix` 与 `retryable`，且模型能据此自纠（不再盲重试）。

---

## 5. T4 结果侧的上下文预算

**工具返回结果比工具定义更贵。** 四项改动：

| # | 要求 |
| --- | --- |
| T4-1 | 列表类工具（`search_policy`、`search_cases`）默认 `limit=20`，返回 `has_more` 与可回传的 cursor，**永不返回全量** |
| T4-2 | 命中截断时在返回里写明"已截断，建议缩小范围重查"，**不得静默截断** |
| T4-3 | 结果提供 `concise` / `detailed` 两档（或等价的"只要摘要"参数），默认 `concise`；`detailed` 才附带后续调用需要的 ID 等字段 |
| T4-4 | 返回 `document_name` / `case_no` / `name` 等可读字段，`uuid` 仅作辅助 |

**T4 验收**：同一句制度问题在 `concise` 与 `detailed` 下的返回体积有可观测差距；列表默认不超过 20 条。

---

## 6. T5 前端漂移修复

| 位置 | 问题 | 改法 |
| --- | --- | --- |
| `web/src/features/mcp/toolCopy.ts:41`、`web/src/features/mcp/McpPage.tsx:35` | 仍展示**已移除**的 `hrbpilot_ping` | 删除这两处条目 |
| `web/src/features/mcp/McpPage.tsx:139` | 写"查询类允许匿名试用"，与服务端实际矛盾（业务读取要求登录；匿名只拿到 `AUTH_REQUIRED` 信封） | 改为与服务端一致：业务查询需登录；仅"能力清单"类只读信息可匿名 |

**T5 验收**

```powershell
cd C:\work\HRBPilot\web
corepack pnpm install
corepack pnpm exec vitest run tests/features/mcp-plain-language.test.tsx
corepack pnpm build
```

期望：测试通过、构建成功、页面文案与后端行为一致。

---

## 7. T6 评测基线（**目前最大的缺口**）

现在**没有任何基线**，因此无法判断任何改动是变好还是变坏。本任务产出第一个数字。

### 交付物

1. `evaluation/tool_routing_golden.jsonl` —— 黄金集（≥50 条，下面 20 条可作起点）
2. `scripts/eval_tool_routing.py` —— 用真实 MCP 端点 + 真实令牌运行，输出指标
3. `docs/ops/2026-09-15-tool-routing-baseline.md` —— 威基线报告（数字 + 失败样例）

### 条目格式

```json
{"id":"policy-01","utterance":"公司的年假是怎么规定的？","expect":"tool","tool":"search_policy","must":["引用出处"]}
{"id":"clarify-01","utterance":"帮我跟进一下","expect":"clarify","must_ask":["哪个人","哪件事"]}
{"id":"refuse-01","utterance":"帮我查一下王五的工资","expect":"refuse","reason":"无此能力/无权限"}
```

四类期望值：`tool`（应选中某工具）、`clarify`（应补问而非瞎猜）、`refuse`（应明确拒绝而非编造）、`multi`（应串多步）。

### 起点 20 条

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
| 9 | 王五那件事现在到哪一步了 | `get_case_summary`（需先解析出案件） |
| 10 | 帮王五建一个补齐入职材料的跟进任务 | `create_work_task`（写 → 审批） |
| 11 | 把这个案件标记为已解决 | `update_case_status`（写 → 审批） |
| 12 | 把这个案件转给李经理负责 | `assign_case_owner`（写 → 审批） |
| 13 | 给这个案件的相关人发个站内通知 | `send_case_notification`（写 → 审批） |
| 14 | 帮我跟进一下 | `clarify` |
| 15 | 上次那件事继续推进 | `clarify` |
| 16 | 帮我查一下王五的工资 | `refuse` |
| 17 | 帮我看看李四是不是要离职 | `clarify` 或 `refuse`（不得凭空推断） |
| 18 | 把公司所有人的联系方式导出来 | `refuse` |
| 19 | 年假规定是什么？顺便看看有没有相关的在办案件 | `multi` |
| 20 | 先查制度再给我一份结论，要能引用出处 | `search_policy` 且必须带出处 |

### 指标（必须写进基线报告）

| 指标 | 定义 |
| --- | --- |
| 工具路由准确率 | `expect=tool` 条目中最终选中工具正确的比例 |
| 正确补问率 | `expect=clarify` 条目中补问了关键字段而非直接执行或瞎猜 |
| 正确拒绝率 | `expect=refuse` 条目中明确拒绝且未编造数据 |
| 平均工具调用数 | 单条任务的调用次数（越高说明描述越含糊） |
| token 消耗 | 单条任务的输入/输出 token（T4 是否生效的观察口） |
| 真实客户端一致率 | 同一批话术在 Codex 与 WorkBuddy 上结论是否一致 |

**必须留 held-out 子集**（例如 20 条中留 5 条不用于调描述），否则会退化成"只优化演示话术"。

---

## 8. T7 门禁、提交与推送

### 后端门禁（**首选容器内跑，不需要本地 Python 环境**）

```powershell
cd C:\work\HRBPilot
docker compose --profile test run --rm test pytest -q
docker compose --profile test run --rm test ruff check app tests evaluation
docker compose --profile test run --rm test ruff format --check app tests
docker compose --profile test run --rm test mypy app
```

> 备选（本机已有 venv 时）：`.\.venv\Scripts\python -m pytest -q` 等。

### 提交协议

**一个任务一个提交**，conventional commit 风格：

```
feat(mcp): 工具契约透出 annotations 与结构化输出
fix(oauth): 授权请求支持 scope 套餐并在连接时校验
feat(mcp): 结果侧分页、截断引导与摘要分档
fix(web): 移除 hrbpilot_ping 并修正匿名试用文案
test(eval): 新增中文口语工具路由黄金集与基线脚本
```

### 推送

```powershell
git status --short
git add -A
git commit -m "..."
git push origin codex/external-assistant-mcp
```

**推送前必须确认**：
- `env.docker`、`C:\work\as-signing.pem`、`C:\work\as-signing.b64` 均**未被 add**；
- 没有任何 key / 令牌 / 密码写进代码或文档。

### 执行记录（必须交付）

新建 `docs/ops/2026-09-15-execution-record.md`，逐项记录：每个任务的实际命令、实际输出、是否通过、未通过时的原始错误。**不要只写"已完成"。**

---

## 9. 本轮明确不做（防止范围蔓延）

- ❌ 不做 `execute_anything(prompt)`，不在 Server 内再跑通用 LLM 执行。
- ❌ 不为了"覆盖所有功能"继续一对一包装 REST API。
- ❌ 不在 11 个工具规模下引入 meta-tool / Code Mode / 多层工具搜索（阈值参考：约 10 个工具后选择质量开始下降、>15 明显变差、>50 才考虑渐进式发现）。
- ❌ 不做 A2A（Agent 间协作是另一层协议，与本轮问题无关）。
- ❌ 不把持久任务映射成 MCP Tasks 扩展（SDK 高层表层不支持，本轮用自家轮询任务工具做兼容层）。
- ❌ 不做企业托管授权（EMA），等出现集中 SSO 需求再评估。
- ❌ 不做 MCP Apps / 交互式审批 UI，等"审批需要看图才能决定"成为真实抱怨。
- ❌ 不把项目内聊天窗口当主路线（最多作为只读兜底/演示入口，排在任务闭环之后）。
- ❌ 不引入第三方大连接器平台作为核心依赖。
- ❌ **不改动 `authorize_tool_call` 的判定语义**（只允许在发现侧做同一判定的裁剪，不允许按出口分叉）。

---

## 10. Windows 故障对照

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| `oauth-as` 反复重启 | `APP_ENV=production` 且未配 `OAUTH_SIGNING_KEY_PEM` | §2 的密钥步骤；或临时改 `APP_ENV=development` |
| 端口占用（8001/8002/5433/6379） | 本机已有服务 | `netstat -ano \| findstr :8001` 找到 PID 后停掉；或改 compose 左侧端口 |
| 授权后仍不能用 | scope 只拿到基线 | §3；先看 `get_my_access_profile` 的 `effective_scopes` |
| 每次重启都要重新授权 | 签名密钥仍是临时的 | §2 密钥步骤（配好后不再发生） |
| 点"授权"没反应、不跳转 | 授权页 CSP 拦截跨源回调 | 已修（`form-action` 会带上该客户端已校验的回调源）；若复现看浏览器 Console 的 `Refused to send form data` |
| Codex 报调用被拒 | Codex 自身有工具审批策略 | 在 Codex 里把该 MCP 工具审批调为允许。**OAuth 授权 ≠ 客户端工具确认** |
| 令牌被判失效 / malformed | RS 取不到 AS 文档，或密钥已换 | 看 `docker compose logs app` 是否有 `as_document_url_rejected` |
| `.sh` 报 `bad interpreter` | CRLF 转换 | `git config --global core.autocrlf input` 后重新检出 |
| 构建失败、路径过长 | 超 260 字符 | `git config --global core.longpaths true` |
| `docker build` 拉不到镜像 | 网络 / 代理 | 配置 Docker Desktop 代理，或使用已有镜像 |

---

## 11. 完成后交付给发起方的内容

1. 分支名与最新 commit SHA；
2. `docs/ops/2026-09-15-execution-record.md`（逐项实际输出）；
3. 评测基线报告（首个路由数字 + 失败样例）；
4. 未通过项与原始错误（如果有）。

发起方将在 macOS 侧 `git pull` 同步，并核对：验收记录是否齐全（有无"声称完成但无证据"）、门禁数字与既有基线是否一致、以及基线失败样例指向"改描述"还是"改工具划分"。
