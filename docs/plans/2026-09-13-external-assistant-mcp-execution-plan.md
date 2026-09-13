# HRBPilot 外部 AI Agent MCP 接入执行方案

> 版本：v1.0  
> 日期：2026-09-13  
> 性质：实施与验收规格，不代表当前功能已经完成  
> 目标读者：承担开发的 AI Agent、代码审查者、发布负责人

## 0. 给执行 Agent 的任务指令

请基于本方案，在独立分支中实现“用户通过自己常用的 AI Agent，以远程 MCP 方式安全调用 HRBPilot”的完整能力。

执行时必须遵守以下约束：

1. 从当前 `origin/main` 的明确提交创建 `codex/external-assistant-mcp` 或等价功能分支，记录 base SHA；不得直接在 `main` 开发或推送。
2. 按本文工作包顺序提交，每个工作包至少一个可独立审查、可回退的提交；数据库迁移和业务代码不要混在一个超大提交中。
3. 先完成只读闭环，再开放提交审批类写工具。任何写工具都只能创建 `ApprovalRequest`，不得绕过审批直接改变业务数据。
4. 不得把“工具列表隐藏”当作权限控制。每次工具执行都必须重新校验身份、租户、角色、OAuth scope、客户端授权上限和对象级 ACL。
5. 不得手写密码学实现。OAuth、JWT、PKCE 和 token 校验优先采用成熟且维护中的标准库；新增依赖必须说明选择依据、维护状态及许可证。
6. 不得在源码、示例、测试输出、日志或提交历史中写入真实 token、client secret、用户数据或生产地址。
7. 每完成一个里程碑，运行对应测试并保存准确命令和结果。若测试失败，不得用“与本次无关”一笔带过，必须给出复现证据和影响判断。
8. 未通过本文“阻断级验收项”前，不得宣称可用于生产，也不得直接合并到 `main`。
9. 完成后按第 13 节准备交付包，交由 Codex 复核和最终验收。

## 1. 目标与范围

### 1.1 用户目标

用户在 WorkBuddy、Codex、Claude Code 等自己日常使用的 AI Agent 中对话并下达任务；外部 Agent 通过标准 MCP 实时调用 HRBPilot，HRBPilot 负责：

- 认证用户和调用客户端；
- 确定唯一租户及用户身份；
- 根据角色、scope、客户端授权上限和对象 ACL 做权限判定；
- 返回最小化、可追溯的 HR 证据和结果；
- 对高风险或写入动作创建审批请求，并提供审批状态查询；
- 记录可审计、可撤销、可限流的调用轨迹。

“实时”在本方案中的含义：

- 检索、读取、校验和创建审批请求为同步返回；
- 审批执行为异步流程，首版通过 `get_approval_status` 轮询和安全深链查询；
- 不在首版承诺跨所有 AI Agent 的主动推送或统一事件订阅。

### 1.2 首期接入优先级

| 优先级 | 客户端/渠道 | 首期目标 |
| --- | --- | --- |
| P0 | WorkBuddy | 完成远程 MCP + OAuth 登录 + 中文 Skill 包 + 真实客户端联调 |
| P0 | Codex | 作为标准 MCP 参考客户端完成认证、发现、只读和审批提交联调 |
| P0 | Claude Code | 作为第二个标准 MCP 参考客户端验证协议兼容性 |
| P1 | 支持自定义远程 MCP 的国内 Agent | 按官方能力逐个认证，不根据宣传推测兼容性 |
| P1 | 飞书/企业微信中的自有机器人或应用 | 复用 MCP 服务和授权核心，但作为渠道适配项目单独排期 |
| P2 | 豆包等未明确开放自定义远程 MCP 的消费级助手 | 等官方开放能力和企业身份方案明确后再接入 |

首版不做：通用聊天 UI、跨助手会话同步、外部 Agent 之间互相转发、无人审批的 HR 数据写入、用浏览器自动化冒充正式协议接入。

## 2. 当前基线与需要修正的缺口

开始编码前，执行 Agent 必须重新验证以下基线；若代码已变化，以实际代码为准并在交付报告中记录差异。

### 2.1 已有能力

- `app/main.py` 已挂载无状态 Streamable HTTP MCP 入口 `/mcp`。
- `app/mcp/server.py` 已定义 MCP 工具，并在工具层解析 JWT。
- 现有只读工具包含 `search_policy`、`get_policy_source`。
- 现有写工具要求 `case_id`，且设计为创建 `ApprovalRequest`。
- `app/mcp/contract.py` 已有稳定结果码，如 `FOUND`、`NO_EVIDENCE`、`AWAITING_APPROVAL`、`AUTH_REQUIRED`、`FORBIDDEN`、`FAILED`。
- RBAC、租户上下文、JWT 登录/刷新、审计辅助函数、审批模型和部分飞书/企业微信连接器已存在。

### 2.2 阻碍外部标准客户端接入的主要缺口

1. `/mcp` 当前绕过认证、RBAC 和限流中间件，认证只发生在工具函数内部。
2. 未认证调用通常得到 HTTP 200 + `AUTH_REQUIRED` 业务包，而标准 MCP OAuth 自动发现需要传输层 HTTP 401 和 `WWW-Authenticate`。
3. 缺少 OAuth 2.1 / PKCE、Protected Resource Metadata、Authorization Server Metadata、动态客户端注册、授权和撤销闭环。
4. 缺少统一的 MCP 调用身份对象，无法稳定表达用户、租户、客户端、安装实例、scope 和凭据来源。
5. MCP 调用未进入现有限流链路；不能简单删除 `/mcp` 豁免，否则中间件状态依赖会导致错误拒绝。
6. 匿名读取目前不返回真实业务数据，README/UI 中“匿名读放行”等表述容易造成误解。
7. 缺少案件搜索/选择和审批状态 MCP 工具，外部 Agent 无法自然完成“找到案件—提交动作—等待审批”的闭环。
8. 缺少按外部客户端、安装实例、credential 和 tool 维度的完整审计及撤销能力。

## 3. 目标架构与安全边界

```text
用户
  │ 在自己的 AI Agent 中对话
  ▼
WorkBuddy / Codex / Claude Code / 其他已认证客户端
  │ MCP Streamable HTTP + OAuth 2.1/PKCE
  ▼
HRBPilot MCP 传输层认证网关
  │ 生成 McpPrincipal
  ▼
统一授权决策
  ├─ 用户角色 capabilities
  ├─ OAuth scopes
  ├─ client / installation 授权上限
  ├─ tenant 隔离
  └─ 对象级 ACL
  ▼
MCP 工具执行层
  ├─ 只读：最小化证据结果
  └─ 写入：仅创建 ApprovalRequest
          │
          ▼
       审批执行器 ── 审计日志 / 撤销 / 限流 / 监控
```

### 3.1 统一身份模型

新增或等价实现不可变的 `McpPrincipal`，至少包含：

```python
McpPrincipal(
    tenant_id: UUID,
    user_id: UUID,
    role: str,
    client_id: str,
    installation_id: UUID,
    scopes: frozenset[str],
    credential_id: UUID | None,
    token_id: str | None,
    auth_method: Literal["oauth", "pat", "internal"],
)
```

关键约束：

- `tenant_id`、`user_id` 和角色只能来自已验证身份，不得接受工具参数或客户端 header 覆盖。
- 外部 Agent 既是用户代理，也是独立、可审计、可撤销的客户端主体。
- 有效权限必须取交集：

```text
effective_permission
= user_role_capabilities
∩ oauth_scopes
∩ client_installation_ceiling
∩ object_acl
```

- discovery 中是否展示工具只影响体验，不构成安全边界。
- 同一授权函数必须被工具发现和工具执行复用，执行阶段必须再次校验。

### 3.2 建议 scope

首版使用少量稳定、面向业务能力的 scope，不把每个工具都变成一个 scope：

| Scope | 能力 |
| --- | --- |
| `hrb:policy:read` | 搜索制度、读取制度来源及引用证据 |
| `hrb:case:read` | 搜索和读取调用者有权访问的案件摘要 |
| `hrb:case:propose` | 为案件提交需审批的建议动作 |
| `hrb:approval:read` | 查询本人/本人客户端发起且有权查看的审批状态 |
| `hrb:profile:read` | 读取最小化的当前身份、租户、角色与授权能力摘要 |

PAT 仅作为内部或兼容性回退：必须短有效期、可撤销、scope 受限；默认不得发放长期写权限 PAT。

## 4. 数据模型与接口契约

### 4.1 数据表/模型

具体命名可以服从现有项目风格，但必须覆盖下列概念：

1. `mcp_clients`
   - `client_id`、名称、客户端类型、redirect URI 白名单、状态；
   - 客户端元数据、创建时间、最后使用时间；
   - public client 不保存无意义的共享 secret；如支持 confidential client，secret 只能保存强哈希。
2. `mcp_installations` 或 `mcp_grants`
   - `tenant_id`、`user_id`、`client_id`、授权 scopes、状态；
   - `created_at`、`last_used_at`、`revoked_at`、撤销原因；
   - 唯一约束防止重复或歧义安装。
3. OAuth authorization code / refresh session
   - authorization code 一次性、短有效期、绑定 `client_id`、redirect URI 和 PKCE challenge；
   - refresh token 只保存不可逆哈希，支持 rotation 和 reuse detection；
   - access token 必须 audience-bound，包含或可解析到 installation 和 scopes。
4. MCP 调用审计
   - `tenant_id`、`user_id`、`client_id`、`installation_id`、`credential_id/token_id`；
   - tool、结果码、耗时、参数摘要哈希、对象引用、审批 ID；
   - 不记录完整 token，不默认记录敏感原文或完整检索内容。

每个迁移都必须提供 upgrade 和 downgrade，并验证空库升级、现有库升级及关键唯一约束。

### 4.2 OAuth / MCP 端点

至少实现或通过已选授权服务器提供：

- `/.well-known/oauth-protected-resource`
- `/.well-known/oauth-authorization-server`
- `/oauth/register`（WorkBuddy 动态客户端注册所需）
- `/oauth/authorize`
- `/oauth/token`
- 建议：`/oauth/revoke`
- 现有 `/mcp` Streamable HTTP 端点

同时按当前 MCP Authorization 规范评估并优先兼容 Client ID Metadata Documents；DCR 作为 WorkBuddy 的明确需求和其他旧版客户端的兼容机制。授权请求和 token 请求必须校验 OAuth Resource Indicators 的 `resource` 参数，并与 `/mcp` 的 canonical resource URI、access token audience 保持一致。

未携带凭据或 token 无效时，`/mcp` 必须在业务工具执行前返回传输层 401，并携带符合规范的 `WWW-Authenticate`，示意：

```http
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer resource_metadata="https://<host>/.well-known/oauth-protected-resource"
```

scope 不足返回 403，并给出可机器理解的错误和需要的 scope。业务层 `AUTH_REQUIRED` / `FORBIDDEN` 可以保留作内部防御和稳定客户端契约，但不能替代标准 HTTP 挑战。

### 4.3 工具结果契约

所有工具继续使用统一 envelope，至少包含：

- 稳定 `outcome_code`；
- 面向用户的简短中文 `message`；
- 结构化 `data`；
- `trace_id`；
- 有证据的读取返回来源 ID、标题、版本/生效时间、引用片段和可追溯链接；
- `next_actions` 只返回调用者实际有权执行的动作；
- 限制结果条数、单段长度和总字符预算，避免把大量敏感上下文交给外部模型。

不得把内部堆栈、SQL、token、完整访问控制规则或其他租户信息返回给外部 Agent。

## 5. 分阶段工作包

每个工作包完成后单独提交。下列文件路径是候选范围；执行 Agent应先通过现有结构确认，避免为了匹配文档而制造不必要目录。

### WP0：冻结基线、ADR 与威胁模型（0.5—1 天）

任务：

- 记录 base SHA、Python/Node/数据库版本和当前测试基线。
- 写 ADR，明确“HRBPilot 是 MCP Resource Server；Authorization Server 采用外部 IdP 还是项目内受审 OAuth 模块”。
- 比较至少两个维护中的 OAuth/OIDC 实现方案，记录协议覆盖、DCR、PKCE、撤销、数据库适配、维护状态和许可证。
- 建立威胁模型：token 泄漏、redirect URI 劫持、跨租户访问、scope 提权、审批重放、工具参数注入、日志泄密、资源枚举、提示注入。

交付：

- `docs/adr/` 中的架构决策；
- `docs/security/` 中的威胁模型或等价位置；
- 基线测试记录。

阻断验收：未选定可信 OAuth 实现、未明确 issuer/audience/资源 URL 和信任边界，不得进入 WP2。

### WP1：统一 MCP Principal 与授权决策（1—2 天）

任务：

- 引入 `McpPrincipal` 和单一授权服务。
- 将现有 JWT 工具级解析收敛到可测试的认证适配层。
- 把角色 capability、scope、client ceiling、租户和对象 ACL 合并为显式决策结果。
- 为拒绝原因定义稳定内部代码，但外部响应不泄漏对象是否存在。
- 保留写工具审批不变量。

候选位置：

- `app/mcp/auth/` 或 `app/mcp/security.py`
- `app/mcp/server.py`
- `app/access/middleware/rbac.py`
- `app/mcp/contract.py`

测试：

- 用户有角色但缺 scope → 拒绝；
- scope 足够但 client ceiling 不允许 → 拒绝；
- 三者允许但对象属于另一租户 → 拒绝且不泄漏存在性；
- discovery 隐藏后手工调用工具 → 执行阶段仍拒绝；
- 写工具永远只创建审批请求。

### WP2：标准 OAuth 2.1 / PKCE 与传输层认证（3—5 天）

任务：

- 实现 Protected Resource Metadata 和 Authorization Server Metadata。
- 支持 public client、Authorization Code + PKCE S256、严格 redirect URI 匹配。
- 支持 WorkBuddy 所需 DCR；限制注册元数据、redirect schemes、速率和滥用。同步支持 Client ID Metadata Documents，或在 ADR 中记录暂不支持的客户端影响和明确回退。
- 对 authorize/token 请求强制校验 `resource`，签发只对 HRBPilot MCP canonical URI 有效的 audience-bound token。
- access token 短有效期，refresh token rotation；实现安装授权撤销。
- 给 `/mcp` 增加传输层认证网关，生成 `McpPrincipal` 并传入工具上下文。
- 重排或拆分中间件，解决当前 `/mcp` 对 Auth、RBAC、RateLimit 的整体绕过，不能只删除豁免列表。
- 保留健康检查和 OAuth metadata 的必要匿名访问，但匿名请求不能读取业务数据。

候选位置：

- `app/main.py`
- `app/access/middleware/auth.py`
- `app/access/middleware/rbac.py`
- `app/access/middleware/rate_limit.py`
- `app/access/routes/` 下新增 OAuth routes
- `app/mcp/server.py`
- `app/config/settings.py`
- `app/data/models/`、`app/data/migrations/versions/`

协议验收：

1. 无 token 请求 `/mcp` → HTTP 401 + 正确 `WWW-Authenticate`。
2. metadata URL 可匿名读取，issuer/resource/audience 一致。
3. PKCE 非 S256、缺 verifier、错误 verifier、redirect URI 不完全一致 → 拒绝。
4. authorization code 二次兑换 → 拒绝。
5. access token 过期、audience 错误、issuer 错误、签名错误 → 401。
6. scope 不足 → 403；不能退化成 HTTP 200 业务错误。
7. 撤销 installation/credential 后下一次调用即失败。
8. refresh token 重放被识别并使相关 token family 失效。
9. 缺失、非 canonical 或指向其他资源的 `resource` → 拒绝，不签发可用于 HRBPilot 的 token。
10. Client ID Metadata Document 的 URL、文档内 `client_id` 或 redirect URI 不一致 → 拒绝。

### WP3：限流、审计、撤销和敏感信息治理（1—2 天）

任务：

- MCP 限流键至少包含 tenant + user + client/installation；OAuth 匿名端点按 IP/client 做独立保护。
- 对 DCR、authorize、token、tool call 分配不同阈值，避免登录流量挤占业务调用。
- 所有工具调用、拒绝、审批提交和凭据撤销写入持久审计。
- 对参数只保存必要字段或稳定哈希；建立字段脱敏规则。
- 管理端提供安装/凭据列表、最后使用时间和撤销入口；若 UI 延后，先提供受 RBAC 保护的 API。

测试：

- 同一用户不同客户端分别限流；同一客户端不能通过伪造 tenant header 换桶。
- 被限流返回标准 429 和重试提示。
- 审计能按 trace ID 串联认证、授权、工具和审批；日志中搜不到测试 token 与 secret。
- 撤销操作本身有审计记录。

### WP4：只读 MCP 最小可用闭环（1—2 天）

任务：

- 修正 `search_policy`、`get_policy_source` 的认证要求和最小化输出。
- 新增 `get_my_access_profile`，仅返回当前身份的安全摘要、可用 scope 和能力，不返回内部权限细节。
- 设定结果数量、引用片段长度、总响应大小及超时预算。
- 明确“没有证据”与“无权限”的响应，避免越权侧信道。
- 修订 README/UI 中“匿名读放行”等不准确描述。

性能目标：

- 非长任务在目标部署环境内尽量 30 秒内响应；
- 超时返回可重试的稳定结果，不留下半完成写入；
- 服务可用性目标先作为监控 SLO，不在未有数据前宣称已经达到 99.9%。

阻断验收：任何匿名业务数据、跨租户证据、无来源回答或敏感全文外泄都阻止进入客户端发布。

### WP5：WorkBuddy Connector + Skill 首发包（1—2 天）

任务：

- 创建独立的 WorkBuddy 连接器发布目录，不把客户端包逻辑混入服务端核心。
- 包含 `connector-meta.json`、`mcp.json`、图标和 `skills/<skill-name>/SKILL.md`。
- `mcp.json` 使用远程 HTTPS `streamableHttp`；只有认证测试证明目标版本需要时才兼容 SSE。
- OAuth 作为默认模式；短期 token 配置只作为内部 POC 回退，并使用占位符/安全输入，不写死 token。
- Skill 用中文说明适用场景、工具选择、审批语义、证据引用和禁止事项。
- 文档标明最低兼容 WorkBuddy 版本；若使用 token 回退，至少验证 4.23.0 或当时官方要求版本。

Skill 必须要求外部 Agent：

- 回答制度问题时展示来源并区分事实与建议；
- 未找到证据时明确说未找到，不自行编造；
- 涉及员工处分、薪酬、终止劳动关系等敏感动作时只提交审批；
- 展示审批 ID、状态和下一步，而不是声称动作已执行；
- 不在对话中回显凭据和内部敏感字段。

### WP6：真实客户端兼容性认证（1—2 天）

按 WorkBuddy、Codex、Claude Code 的真实客户端逐一验证，而不是只用 curl 或单元测试推断兼容。

每个客户端至少验证：

1. 从全新未授权状态添加远程 MCP。
2. 收到 401 后自动发现 metadata。
3. 浏览器登录、授权、PKCE 回调成功。
4. 能发现允许的工具并完成制度搜索和来源读取。
5. scope 不足或角色不足时获得可理解的拒绝。
6. access token 过期后可按客户端支持方式恢复。
7. 在 HRBPilot 撤销授权后，客户端下一次调用失败并可重新授权。
8. 客户端日志和截图经过脱敏。

输出兼容矩阵：

| 能力 | WorkBuddy | Codex | Claude Code |
| --- | --- | --- | --- |
| Streamable HTTP | 待实测 | 待实测 | 待实测 |
| OAuth metadata discovery | 待实测 | 待实测 | 待实测 |
| DCR | 待实测 | 待实测 | 待实测 |
| PKCE callback | 待实测 | 待实测 | 待实测 |
| token refresh/re-auth | 待实测 | 待实测 | 待实测 |
| structured tool result | 待实测 | 待实测 | 待实测 |
| 中文 Skill/指令 | 待实测 | 不适用或待适配 | 不适用或待适配 |

不能把某一客户端特有字段写进 MCP 核心契约；通过薄适配包处理差异。

### WP7：案件上下文与审批闭环（2—3 天）

只在 WP1—WP6 全部通过后开始。

新增或补全工具：

- `search_cases`：按调用者 ACL 返回最小化案件列表，默认不返回完整员工隐私字段；
- `get_case_summary`：读取单个有权案件的必要上下文；
- `get_approval_status`：只能查询调用者/客户端有权查看的审批；
- 现有提议型工具继续返回 `AWAITING_APPROVAL`、approval ID、过期时间和安全深链。

审批请求必须绑定：

- tenant、user、client、installation；
- tool 名称和规范化参数哈希；
- case/object ID；
- 发起时间、过期时间和幂等键；
- 发起时的权限快照或足够审计证据。

批准执行前必须再次校验审批状态、对象租户、参数哈希、有效期及当前授权；审批不可跨工具、跨参数、跨对象重放。

阻断测试：

- 外部 Agent 不能凭猜测 `case_id` 读取其他用户/租户案件；
- 重复提交带相同幂等键不创建多个审批；
- 修改任意参数后旧审批不可复用；
- 已撤销用户或 installation 的待审批动作不得执行；
- 工具响应不得把“已提交审批”描述为“已完成业务动作”。

### WP8：生产加固与灰度发布（2—4 天）

任务：

- HTTPS、可信代理、Host/Origin 校验、CORS 最小化、CSRF/点击劫持保护。
- 密钥轮换、token 签名 key rotation、旧 key 验证窗口和应急撤销。
- OAuth 与 MCP 指标：认证成功率、401/403/429、工具耗时、失败率、审批创建率、撤销传播时间。
- 告警阈值和 runbook：IdP 故障、token 泄漏、跨租户告警、异常 DCR、限流暴增。
- 功能开关和 allowlist：先内部租户，再小范围客户，再公开申请。
- 回滚必须能关闭外部 MCP、撤销所有外部 installation，且不影响 HRBPilot 内部登录和审批数据。

发布门禁：

- 安全测试和跨租户测试全通过；
- 三个 P0 客户端至少两个完全通过，WorkBuddy 必须通过；
- 无 P0/P1 已知漏洞；
- 迁移、备份、回滚演练有记录；
- 文档没有默认写权限 PAT 或“匿名可读业务数据”的指引。

## 6. 测试与验收矩阵

### 6.1 后端自动化

新增测试建议按职责拆分：

- `tests/mcp/test_transport_auth.py`
- `tests/mcp/test_oauth_metadata.py`
- `tests/mcp/test_oauth_pkce.py`
- `tests/mcp/test_principal.py`
- `tests/mcp/test_authorization_matrix.py`
- `tests/mcp/test_rate_limit.py`
- `tests/mcp/test_audit.py`
- `tests/mcp/test_read_tools.py`
- `tests/mcp/test_case_tools.py`
- `tests/mcp/test_approval_binding.py`

最低命令：

```bash
.venv/bin/python -m pytest -q tests/mcp
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy app
```

不得只跑新测试。最终交付还需运行完整后端测试；若依赖 PostgreSQL/Redis/对象存储，先启动 Compose 并记录健康状态。

### 6.2 前端和管理端

若新增授权确认、installation 管理、撤销或审批深链页面，至少运行：

```bash
corepack pnpm --dir web lint
corepack pnpm --dir web test:run
corepack pnpm --dir web build
```

补充针对授权确认、scope 展示、撤销和审批状态的组件/端到端测试。

### 6.3 必测权限组合

至少覆盖：

- 租户管理员、HRBP、只读审计/观察角色、无权限普通用户；
- 正常 scope、缺 scope、超额 scope、已撤销 grant；
- 正常 client、被禁用 client、不同 installation；
- 本租户对象、他租户对象、不存在对象；
- 并发重复请求、幂等重试、过期审批、参数篡改；
- 过期 token、错误 audience、错误 issuer、refresh 重放。

## 7. 客户端差异与兼容策略

统一核心只依赖 MCP 与 OAuth 标准，差异放在连接器包、Skill 或配置说明中：

- WorkBuddy：优先使用官方 Connector + Skill 包；重点验证 DCR、PKCE 自定义 scheme/loopback 回调和中文工具说明。
- Codex：作为通用 MCP 客户端验证远程服务配置、认证发现和结构化工具结果；不依赖 WorkBuddy 专有 manifest。
- Claude Code：验证其支持的远程 MCP 配置和认证流程；若某版本缺某能力，记录最低版本和有限回退方式。
- 国内其他 Agent：只有在官方文档确认可配置自定义远程 MCP、认证方式和企业数据处理边界后才进入兼容列表。
- 飞书/企业微信：现有 connector registry 可复用通知/入口能力，但 bot 渠道不应与 MCP 身份混成同一个信任主体；需显式身份绑定。

禁止为追求“看起来都支持”而把长期 bearer token 写入用户配置，或使用共享企业 token 代表所有员工。

## 8. 时间计划

以下为单个熟悉项目的开发者或高质量 Agent、有人及时复核时的估算：

| 阶段 | 工作包 | 预计时间 | 可交付结果 |
| --- | --- | --- | --- |
| 阶段 A | WP0—WP1 | 1.5—3 天 | 安全边界、统一身份和授权决策 |
| 阶段 B | WP2—WP4 | 5—9 天 | 安全的远程只读 MCP 核心 |
| 阶段 C | WP5—WP6 | 2—4 天 | WorkBuddy 首发包和三客户端认证 |
| 阶段 D | WP7 | 2—3 天 | 案件与审批状态闭环 |
| 阶段 E | WP8 | 2—4 天 | 灰度、监控、回滚和生产加固 |

整体判断：

- 内部只读原型：约 1—2 周；
- 安全的公开只读 Beta：约 3—5 周；
- 包含案件和审批闭环：累计约 6—9 周；
- 各客户端市场审核、企业部署和第三方平台审批时间另计。

如果由多个 Agent 并行，WP2 的认证核心、WP4 的工具契约、WP5 的 WorkBuddy 包可以在接口冻结后部分并行，但数据库模型、中间件和授权服务必须由单一负责人集成，避免产生两套身份来源。

## 9. 关键风险与停止条件

| 风险 | 影响 | 缓解/停止条件 |
| --- | --- | --- |
| 自研 OAuth 实现不完整 | token 泄漏、账户接管 | 使用成熟库/外部 IdP；协议负例未过即停止发布 |
| `/mcp` 中间件重排错误 | 全部 401/403 或绕过限流 | 单测覆盖中间件顺序和 request state；保留回滚开关 |
| 用户权限与客户端权限混淆 | Agent 获得超出用户的能力 | 强制权限交集；client/installation 独立可撤销 |
| 工具发现被误当安全控制 | 手工调用越权 | 每次执行重新授权；加入直接调用负例 |
| 外部模型得到过量 HR 数据 | 隐私与合规风险 | 字段最小化、结果预算、ACL、审计、敏感工具审批 |
| 审批被重放或参数替换 | 未授权写入 | 绑定参数哈希、对象、client、用户、时效和幂等键 |
| 客户端 OAuth 实现差异 | 某助手无法登录 | 真实客户端认证矩阵；薄适配，核心不私有化 |
| 长期 PAT 扩散 | 无法追责、凭据泄漏 | PAT 仅回退、短有效期、最小 scope、可撤销 |
| 文档承诺高于实际 | 用户误判匿名访问/实时执行 | README 与实际行为同步；不虚报 SLA 和第三方兼容 |

出现下列任一情况，执行 Agent 必须停止继续扩展，先修复或交由人工决策：

- 可匿名读取任何真实业务数据；
- 任意跨租户读写；
- 外部 Agent 可以绕过审批直接写；
- token、secret 或员工敏感数据进入日志/提交；
- OAuth 负例无法稳定拒绝；
- 迁移不可回退或会破坏现有数据；
- 为兼容单一客户端而破坏标准 MCP 客户端行为。

## 10. Git 与提交要求

建议提交序列：

```text
docs(mcp): record external assistant architecture and threat model
feat(mcp): add canonical principal and authorization decisions
feat(auth): add OAuth metadata PKCE and client registration
feat(mcp): enforce transport authentication and scoped rate limits
feat(mcp): harden read tools and evidence budgets
feat(workbuddy): add connector and Chinese skill package
feat(mcp): add case discovery and approval status tools
feat(admin): add MCP installation revocation controls
test(mcp): add cross-client security and approval regression suite
docs(mcp): publish setup compatibility and incident runbooks
```

要求：

- 每个提交可单独解释，避免生成式大提交。
- 不改写用户已有提交，不做 destructive reset。
- 不提交 `.env`、真实 OAuth client 配置、生产证书或测试录屏中的 token。
- 合并前使用 PR 或等价 review；不得自行直接推送 `main`。

## 11. 文档交付

至少更新或新增：

- README：能力边界、外部助手入口、审批语义、无匿名业务读取；
- 管理员部署文档：issuer、public URL、HTTPS、redirect URI、key rotation、撤销；
- 用户接入文档：WorkBuddy、Codex、Claude Code 分别配置；
- WorkBuddy Connector/Skill 安装说明；
- scope 与角色矩阵；
- 故障排查：401、403、429、回调失败、token 过期、审批等待；
- 安全响应 runbook：凭据泄漏、client 禁用、全量 installation 撤销；
- 兼容矩阵必须写明实际验证版本和日期。

所有截图、日志和示例使用虚构租户、用户和案件，不出现真实个人信息。

## 12. 最终 Definition of Done

只有全部满足，才可申请合并：

- [ ] `/mcp` 未认证请求按标准返回 401 + OAuth resource metadata challenge。
- [ ] OAuth Authorization Code + PKCE S256、DCR、refresh rotation、撤销通过正负测试。
- [ ] `McpPrincipal` 唯一确定 tenant/user/client/installation/scopes。
- [ ] 有效权限按角色、scope、client ceiling 和对象 ACL 的交集计算。
- [ ] MCP 调用受限流、审计和凭据撤销控制。
- [ ] 匿名用户无法读取任何真实 HR 业务数据。
- [ ] 制度检索输出含来源、版本/时间和受控引用片段。
- [ ] 案件工具通过跨租户、对象枚举和最小字段测试。
- [ ] 所有写工具仅创建审批，审批绑定参数哈希并防重放。
- [ ] WorkBuddy 真实客户端完成从安装、登录到工具调用的闭环。
- [ ] Codex、Claude Code 完成参考兼容测试，差异有记录。
- [ ] 后端全量测试、ruff、format check、mypy 通过。
- [ ] 前端 lint、测试和 build 通过（如有前端变更）。
- [ ] 数据库升级、降级、现有数据兼容和回滚演练通过。
- [ ] 文档与实际行为一致，无真实密钥和敏感数据。
- [ ] 没有未关闭的 P0/P1 安全问题。

## 13. 交给 Codex 验收时必须提供的交付包

开发 Agent 完成后，请把以下内容一次性交付，缺失项会降低验收效率：

1. 仓库地址、分支名、base SHA、HEAD SHA。
2. 按工作包列出的提交清单和每个提交的目的。
3. `git diff --stat <base>...HEAD` 及完整变更文件清单。
4. 数据库迁移文件、upgrade/downgrade 命令和实际结果。
5. 全部测试命令、退出码、失败日志；不得只给“已通过”结论。
6. 脱敏后的 HTTP 协议证据：401 challenge、metadata、PKCE、token、403、429、撤销后 401。
7. WorkBuddy、Codex、Claude Code 的实际版本、配置、成功截图/日志和已知差异。
8. 跨租户、scope 提权、工具直调、审批重放、token 重放等负例结果。
9. 新增依赖及其版本、用途、许可证和维护风险。
10. 已知限制、暂缓项、灰度与回滚步骤。
11. 明确声明：提交和日志中没有真实 token、secret 或个人敏感数据。

Codex 验收将依次进行：代码与迁移静态审查 → 身份/权限矩阵复核 → 自动化测试 → OAuth 协议负例 → 三客户端真实联调 → 写审批重放测试 → 回滚演练。未通过阻断项时只给出修复清单，不批准合并。

## 14. 官方参考

- WorkBuddy Connector 与 MCP/OAuth：<https://open.workbuddy.cn/docs/connector>
- OpenAI Codex MCP：<https://developers.openai.com/codex/mcp/>
- Claude Code MCP：<https://code.claude.com/docs/en/mcp>
- MCP Authorization：<https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization>
- MCP Transports：<https://modelcontextprotocol.io/specification/2025-06-18/basic/transports>

实现时应再次核对上述官方文档的最新版本。若规范与本文冲突，以当时正式规范、安全要求及真实客户端测试为准，并在 ADR 中记录偏差。
