# HRBPilot 端到端整体改动方案（评审稿）

> [!IMPORTANT]
> 本文保留为 2026-09-04 前的评审输入，不再作为实施入口。经工程评审确认后的可执行方案见：
> [HRBPilot 端到端整体改动方案（v2 可执行版）](<./HRBPilot 端到端整体改动方案（v2 可执行版）.md>)。
> 若两份文档存在冲突，以 v2 的架构决策、阶段 Gate、测试矩阵和范围边界为准。

<aside>
🎯

**评审结论**：建议按“受控 HR Agent 平台化”推进，而不是全面多 Agent 或微服务重写。短期优先闭环真实业务旅程、统一 Agent/Tool/Context、修复流式安全与会话上下文；长期再扩展 MCP、跨渠道与弹性架构。

</aside>

## 1. 方案范围与评审口径

- **核心用户**：HR 经办人（HRBP/HR 专员）、HR 经理、企业员工；管理员使用独立管理面。
- **核心业务链**：员工提问/提交请求 → AI 分诊与制度取证 → HR 复核方案 → 人工审批 → 受控执行 → 通知与关闭 → 反馈/Eval。
- **实施约束**：当前为单人 + Codex/Cursor 辅助开发；短期保持模块化单体，不为“Agent”概念重写已可用的 RAG、RBAC、审计与 HR Case 状态机。
- **优先级**：P0 = 上线/安全阻塞；P1 = 试点必需；P2 = 规模化增强；P3 = 长期平台化。
- **指标说明**：本文给出的延迟、吞吐等数字是**建议 SLO**，不是当前实测值；需通过压测建立基线。

## 2. 目标分层架构

```
L5 运营与进化层     Eval Registry / Feedback / Drift / Champion-Challenger / 发布门禁
             ↑
L4 体验与渠道层     Employee Portal / HR Workbench / Manager View / Admin / 企微飞书 / MCP
             ↑
L3 业务服务层       Request / Case / Approval / Knowledge / Interview / Voice / Weekly Report
             ↑
L2 Agent 能力层     Agent Runtime / Context Manager / Model Router / Prompt Registry / RAG
             ↑
L1 受控执行层       Tool Gateway / Schema / Policy / Idempotency / Outbox / Audit / Connector
             ↑
L0 信任与数据层     Identity / RBAC+ACL / Tenant / PostgreSQL / Redis / Milvus / MinIO / KMS
```

**核心依赖原则**：

1. UI 不直接调用模型或连接器，只调用业务服务。
2. Agent 不直接访问数据库/HTTP 客户端，只能调用 Tool Gateway。
3. 所有写工具必须经过授权、参数校验、审批、幂等与审计。
4. Context Manager 依赖身份与数据权限；不得把跨租户、跨员工或未授权材料放入上下文。
5. Eval 读取脱敏后的运行轨迹和反馈，发布新 Prompt/模型时反向约束 LLM 层。

---

# 3. UI / 前端交互体验

## 3.1 现状问题

- 已按 `employee / hrbp / hr_manager / admin` 分出三种产品体验，但 HR 工作台导航仍以“制度问答、面谈纪要、员工声音、周报、文化内容”等功能组织，用户容易先选工具、后想业务目标。
- `TodayPage` 已有“继续工作/需要处理/今天完成”，但缺少统一 Case 列表、Case 详情、审批中心与执行回执。
- 员工请求以卡片 + 行内表单处理，适合轻量请求，不足以承载跨日、多人、敏感、需要证据与审批的 Case。
- 后端 Agent 状态（如 `EVIDENCE_READY / PLAN_READY / EXECUTING`）不适合直接作为 HR 业务进度。
- 制度问答前端宣称历史追问携带上下文，但后端目前只用 `session_id` 保存/读取历史，没有把历史消息传给 LLM，存在体验与实际行为不一致。
- 流式问答先向浏览器发送原始 Token，输出护栏在完整生成后才检查；如果最终内容被修正/拦截，用户可能已看到原始输出。

## 3.2 目标

- 从“AI 功能大厅”转为“今日行动 + 事项推进 + 人工决策”的 HR 工作台。
- 员工只看到问答、本人请求和补材料；HRBP 看到队列和 Case；经理看到审批、风险和负载；管理员看到配置、质量与审计。
- 任何 AI 建议都显示来源、缺失事实、风险和可撤销的下一步。

## 3.3 具体改动

### P0｜修复交互真实性与流式安全

- 会话 UI 在 Context Manager 接通前，不再宣称“携带之前上下文”；接通后显示本轮采用了哪些历史信息。
- 对高风险场景采用“句段缓冲后流式”或“生成完成后一次性显示”；低风险场景按句子做快速护栏，不直接裸流 Token。
- 增加统一错误状态：可重试、需补材料、服务降级、转人工、已恢复；禁止统一显示“服务异常”。
- 持久化内容必须与用户最终看到的内容一致，处理好停止生成、护栏修订和网络中断。

### P1｜新增三类核心页面

- `/cases`：事项队列；字段包括风险、业务状态、下一步、等待对象、负责人、计划时间和最近更新。
- `/cases/:id`：Case 工作区；主区域显示事实、材料、时间线和处理结果，右侧显示 AI 摘要、证据、缺口和建议方案。
- `/approvals`：待审批中心；展示动作、影响对象、参数差异、依据、风险、过期时间和“批准/拒绝”。

### P1｜重组导航

- **工作**：今日工作、事项队列、待我审批、工作任务。
- **知识与材料**：制度问答、面谈与记录、员工声音。
- **输出**：HR 周报、内容工具。
- 管理员继续使用完全独立的管理后台。

### P1｜业务状态与 Agent 状态分离

- 增加 `business_status` 与 `agent_run_status` 两套状态。
- 业务状态建议：待受理、待补充、处理中、待审批、待确认、已完成、已转人工。
- Agent 状态只在“运行详情”中展示，默认翻译为业务语言。

### P2｜跨场景闭环

- 制度问答：未解决 → 一键转请求/添加到已有 Case。
- 面谈纪要：选择行动项 → 创建任务/添加到 Case。
- 员工声音：聚合信号 → 人工选择后创建调查任务，禁止自动逐条建 Case。
- HR 周报：由真实 Case、任务和风险聚合生成，可回链到对象。

## 3.4 预期收益

- 降低功能选择成本，HR 登录即可知道“先处理什么”。
- AI 输出从一次性文本变为可跟踪、可审批、可关闭的业务结果。
- 降低敏感信息误展示与流式不安全输出风险。

## 3.5 依赖

- 依赖后端 Case 列表/更新 API、业务状态模型、Context Manager、统一错误码和 Agent Run 事件流。

---

# 4. 后端服务与架构

## 4.1 现状问题

- 当前是结构清晰的模块化单体，场景各自拥有 orchestrator/config/prompt/schema，但缺少统一 Agent Runtime 和跨场景 Tool Contract。
- HR Case 已有状态机、工具白名单、审批和轨迹，是良好基础；生产写工具执行器仍未完整接通。
- 企微/飞书连接器已有注册、签名、凭据和增量同步框架，但部分同步仍在 HTTP 请求中执行，后台 worker 未统一。
- Pipeline 使用进程内 `asyncio.create_task` 写审计、Token 和 Eval；进程退出时可能丢任务，无法保证可靠投递。
- 五个场景与 HR Case 的运行记录、错误、成本和评测口径不统一。

## 4.2 目标

- 保持模块化单体，增加可替换的 Agent/Tool/Context/Model 层。
- 让“建议型能力”和“写入型能力”共享一套授权、审批、幂等、审计和运行轨迹。
- 外部平台只能作为渠道或执行适配器，PostgreSQL 业务状态仍是唯一可信来源。

## 4.3 具体改动

### P0｜统一 Tool Gateway

建立：

```
ToolDefinition
- name / version / input_schema / output_schema
- kind: read | write
- required_capability
- risk_level
- approval_policy
- timeout / retry_policy

ToolContext
- tenant_id / user_id / role / object_scope
- request_id / agent_run_id / case_id

ToolResult
- status / summary / data / external_ref / retryable / error_code
```

- 将现有 `TOOL_SCHEMAS/TOOL_KINDS/register_tool_executor` 迁入统一 Gateway。
- 写工具执行前固定执行：Schema 校验 → 能力校验 → 对象权限 → 审批校验 → 参数哈希 → 幂等 Claim → 外部执行 → 结果回写。
- 首批接通通知、建单、指派、状态更新；真实下游未接通时继续显式返回 501，不做模拟成功。

### P0｜可靠事件与任务

- 使用 Transactional Outbox 替换进程内 fire-and-forget 审计/Eval/通知。
- Celery 消费 Outbox，增加重试、DLQ、任务租约、超时和幂等键。
- Connector 同步统一转为异步任务；HTTP 只负责接受请求并返回 task/run ID。

### P1｜统一 Agent Runtime

- 公共模型：`AgentRun → Plan → Step → ToolExecution → Approval → Event`。
- 现有五个场景包装为 Runtime 的固定工作流节点，不立即改造成多 Agent。
- HR Case 状态机继续作为业务强约束；Runtime 负责调度和观测，不直接修改业务状态。
- 增加取消、暂停、继续、人工接管和运行版本。

### P1｜补齐 Case 服务

- 新增 Case 列表、筛选、负责人、下一步、等待对象、截止时间、参与人、保密范围、结果、关闭与重开 API。
- 增加材料、备注和对外可见更新的显式边界。

### P2｜协议与生态

- 发布 MCP Server：先只读工具，再开放“创建审批请求”类写工具。
- n8n/企微/飞书通过 Gateway 和 Outbox 接入，不能直接写业务表。
- 只有当模块出现独立扩缩容、故障域或发布节奏需求时，再拆成服务；短期不做微服务化。

## 4.4 预期收益

- 减少五套场景重复逻辑；增加新工具不再复制审批与审计代码。
- 避免后台任务因进程退出丢失。
- 为 MCP、n8n 和企业渠道提供稳定接口，同时守住 HR 业务边界。

## 4.5 依赖

- 依赖身份/RBAC/ACL、统一错误模型和数据库迁移；UI、容灾和 Eval 均依赖 Runtime 事件模型。

---

# 5. 性能：延迟、吞吐与资源占用

## 5.1 现状问题

- 已有异步 FastAPI、AsyncPG、Celery，以及 dense/sparse 并发检索基础，但缺少正式延迟分段、压测基线和容量模型。
- LLM 调用设置 60 秒超时和 2 次 SDK 重试，容易在供应商故障时放大尾延迟。
- Prompt、查询改写、Embedding、检索结果缺少统一缓存和版本失效策略。
- Eval、审计和成本记录分散在同步/进程内后台路径，存在主链抖动或丢失风险。
- 长文本场景资源成本明显高于问答类场景，但缺少按场景的动态预算。

## 5.2 建议 SLO（需压测确认）

| 链路 | 短期目标 |
| --- | --- |
| 非 LLM API p95 | ≤ 300 ms |
| Case 列表 p95 | ≤ 500 ms |
| Hybrid Retrieval p95 | ≤ 800 ms |
| 制度问答首个安全句段 p95 | ≤ 2.5 s |
| 制度问答完整回答 p95 | ≤ 12 s |
| 异步任务受理 p95 | ≤ 500 ms |
| 写工具审批后执行成功率 | ≥ 99%，不含下游明确拒绝 |

## 5.3 具体改动

### P0｜建立可观测基线

- 统一记录 `request_id / tenant / scenario / run / provider / model / prompt_version / kb_version`。
- 分段指标：认证、DB、查询改写、dense、sparse、融合、首 Token、完整生成、护栏、持久化。
- 建立 Locust/k6 场景：普通 API、SSE 并发、文件上传、队列峰值、供应商超时。

### P1｜降低延迟与资源

- 缓存 Query Rewrite、Embedding 和检索结果，key 必须包含 tenant、KB 版本、权限范围和 Prompt/模型版本。
- 限制上下文块数量与单块长度；优先 MMR/去重，减少重复证据。
- 对周报/文化内容使用异步生成，不占用在线 Web worker。
- SSE 增加背压、断开检测和最大并发；断开后及时取消上游模型请求。
- 按场景配置 max_tokens、超时和模型档位；低风险分类/改写使用小模型。

### P2｜容量与弹性

- Web、Agent Worker、Ingestion Worker、Eval Worker 分开部署和扩缩容。
- 对 LLM/Embedding/Vector DB 使用独立连接池、并发信号量和舱壁隔离。
- 建立租户级配额：请求、并发流、Token、文档体积、任务积压。

## 5.4 预期收益

- 降低长尾延迟和供应商故障造成的线程/连接耗尽。
- 可用数据决定是否拆服务或扩容，避免单人团队过早复杂化。
- 通过上下文压缩与模型分层降低 Token 和资源成本。

## 5.5 依赖

- 依赖 Runtime 统一标识、Model Router、Context Manager、Outbox 和指标采集。

---

# 6. 安全：认证、授权与数据保护

## 6.1 现状问题

- 已有 JWT issuer/audience/type 校验、能力型 RBAC、对象级服务校验和租户边界；管理员默认不继承 HR 业务权限，这是正确基础。
- 角色/权限写在 Token 中，权限变更在 Token 到期前可能滞后。
- HS256 单密钥适合当前部署，但不适合长期 SSO、密钥轮换和多服务验证。
- Prompt Injection 主要依赖正则，输出事实性检查默认关闭；不能覆盖间接注入、编码变体和工具诱导。
- 输入日志与审计可能包含 HR 敏感信息；需要明确最小记录、脱敏和保留策略。
- RAG 内容当前被拼接进 system prompt，文档中的指令可能获得过高优先级。

## 6.2 目标

- 零信任 Tool 调用：身份、能力、对象范围和数据可见性逐次校验。
- 数据最小化、字段级保护、可撤销授权和可证明审计。
- 把文档、聊天和外部消息始终视为不可信数据，而不是指令。

## 6.3 具体改动

### P0｜授权一致性

- Tool Gateway 强制校验 capability + object ACL + tenant；禁止只依赖路由中间件。
- Token 增加 `permissions_version` 或服务端 Session 版本；用户禁用/权限撤销立即失效。
- 写工具审批人不得与发起人冲突的场景增加 SoD 规则。
- Webhook 保持平台签名验证，并增加时间窗、nonce、重放表和来源 IP/证书策略（按平台能力）。

### P0｜Prompt 与工具安全

- RAG 文档从 system prompt 移出，放入明确标注的“不可信证据”段；系统指令明确禁止执行证据中的命令。
- 工具参数仅接受服务器端 Schema；模型不能指定 tenant、approver、权限或内部 URL。
- 高风险 HR 类别只能取证和转人工，禁止自动生成最终用工决定。

### P1｜数据保护

- Case、面谈、投诉、医疗/薪酬等字段增加敏感等级和字段级加密。
- Connector 主密钥迁移至 KMS/Vault，支持版本和轮换；密钥不进入日志与前端。
- 定义数据保留、导出、删除、员工离职后的访问撤销和附件生命周期。
- 审计日志只记录必要摘要或哈希引用；原始敏感内容存受控对象，不复制到多个日志系统。

### P2｜企业身份

- 接入 OIDC/SAML SSO，JWT 改为 RS256/ES256 + JWKS。
- 加入 MFA/Step-up Authentication：审批高风险工具时二次认证。
- 长期增加审计防篡改（哈希链/WORM）与定期权限复核。

## 6.4 预期收益

- 降低跨租户、越权、间接注入和敏感日志泄漏风险。
- 支撑企业 SSO、审计和合规评审。

## 6.5 依赖

- L0 身份与密钥是 Tool Gateway、Context、UI 可见性和 Eval 数据治理的前置依赖。

---

# 7. LLM 集成、提示策略与对话上下文

## 7.1 现状问题

- 已支持多个 OpenAI-compatible Provider 和运行时切换，但主调用仍直接使用当前 Provider；已有 fallback 模块没有接入主要 orchestrator。
- fallback 实现通过全局变量切换活动 Provider，并发时可能出现不同请求相互影响。
- Provider 配置是全局的，缺少按场景、风险、租户和任务类型路由。
- 会话历史被保存，但未真正进入 LLM 上下文；前端“带上文追问”与后端不一致。
- RAG 证据被渲染到 system prompt，扩大间接注入风险。
- Prompt 是场景配置文本，缺少版本、实验、发布门禁和快速回滚。

## 7.2 目标

- 建立无共享可变状态的 Model Router。
- 建立可审计、可裁剪、权限安全的 Context Manager。
- Prompt/模型/检索策略均版本化，并通过 Eval Gate 发布。

## 7.3 具体改动

### P0｜Model Router

- 每次请求生成不可变 `ModelRequest`，显式指定 provider/model/timeout/retry/fallback。
- 禁止通过全局 `_ACTIVE_PROVIDER` 做请求级切换。
- 路由维度：场景、风险、延迟要求、上下文长度、租户策略和成本预算。
- 重试只处理超时、连接中断和可重试 5xx；429 使用 Retry-After/切换 Provider，4xx 参数错误不重试。

### P0｜Context Manager

上下文分层：

```
1. System Policy：角色、安全和不可越过的规则
2. Task Context：本场景目标、输出 Schema、业务状态
3. Conversation Memory：最近相关轮次 + 已确认事实摘要
4. Retrieved Evidence：带来源、权限、版本、时间的不可信证据
5. Tool Results：结构化、可验证的结果
6. Current User Message：当前请求
```

- 近期窗口建议保留最近 4–8 轮；较早内容压缩为“已确认事实/用户偏好/未解决问题”，不做全文无限累计。
- 摘要必须可追溯到 message ID；用户更正后使旧摘要失效。
- 检索 Query 只引入必要的上一轮指代信息，不把全部历史拼接进检索。
- 上下文预算按场景配置；达到上限时优先保留系统策略、当前问题和高质量证据，先压缩历史。
- 不同 Case/Session、员工与租户之间严格隔离；敏感字段按当前用户权限裁剪。

### P1｜Prompt Registry

- PromptSpec：`name/version/scenario/risk/output_schema/allowed_tools/eval_suite/status`。
- System、Task、Evidence 和用户输入分别管理，禁止字符串无边界拼接。
- Agent 计划使用严格 JSON/Pydantic；解析失败允许一次结构化修复，再失败转人工。
- 为政策问答定义“有证据回答/无证据拒答/需要 HR 复核”三类输出契约。
- Prompt 变更必须生成差异、评测结果和回滚点。

### P2｜记忆与个性化

- 短期只做 Session/Case 记忆，不做开放式个人长期记忆。
- 长期记忆仅保存明确授权、稳定且有业务用途的信息，并提供查看、修改和删除入口。

## 7.4 预期收益

- 修复连续对话名实不符的问题。
- 降低上下文膨胀、跨会话泄漏和间接 Prompt Injection。
- 提高模型切换、成本优化和 Prompt 回滚能力。

## 7.5 依赖

- Context Manager 依赖身份、ACL、消息/Case 数据模型；Prompt 发布依赖 Eval Gate；UI 会话体验依赖 Context 的真实接入。

---

# 8. 出错检测、回退与容灾

## 8.1 现状问题

- 已有统一 AppError、request ID、LLM 429/连接错误映射、Celery 超时和 HR Case 幂等基础。
- LLM fallback 未真正接入主链；返回降级文本与抛错的策略不统一。
- 缺少端到端错误分类、熔断、舱壁、DLQ、Outbox 和自动恢复演练。
- 流式连接中断、护栏改写、持久化失败可能导致“用户所见、数据库记录、评测输入”不一致。

## 8.2 目标

- 每个错误都能回答：发生在哪一层、是否重试、是否切换、用户看到什么、数据是否已提交、如何恢复。
- 任何失败都不重复副作用、不伪造成功、不让任务永久 pending。

## 8.3 具体改动

### P0｜统一错误分类

```
VALIDATION / AUTH / PERMISSION / POLICY_BLOCKED
DEPENDENCY_TIMEOUT / RATE_LIMIT / UNAVAILABLE
MODEL_BAD_OUTPUT / NO_EVIDENCE / CONTEXT_OVERFLOW
TOOL_REJECTED / TOOL_FAILED / IDEMPOTENCY_CONFLICT
TASK_EXPIRED / PARTIAL_RESULT / INTERNAL_BUG
```

每类定义：HTTP 状态、重试性、用户文案、告警级别、回退动作和审计字段。

### P0｜可靠写入与恢复

- 写工具使用幂等键 + Claim-before-side-effect + 结果回查。
- Outbox 负责通知/Eval/审计可靠投递；失败进入 DLQ，可人工重放。
- 任务增加 heartbeat、租约、最大尝试、取消和过期清理。
- UI 显示“未执行/可能已执行待核对/已恢复”，禁止模糊的“失败”。

### P1｜分层降级策略

| 故障 | 回退策略 |
| --- | --- |
| Dense/Milvus 不可用 | 切 sparse；证据阈值不足则拒答，不伪装混合检索成功 |
| Sparse/PostgreSQL 检索不可用 | 切 dense；仍保留租户与 KB 过滤 |
| Embedding 不可用 | 使用已缓存向量/纯 sparse；禁止无证据生成政策结论 |
| 主 LLM 不可用 | Model Router 切备用 Provider；仍失败则展示检索证据或创建人工请求 |
| 输出 Schema 无效 | 一次结构修复；失败转人工，不执行工具 |
| Connector 不可用 | 保存待发送 Outbox，通知用户已受理但暂未送达 |
| Eval 不可用 | 不阻塞主业务；记录待评测事件并补跑 |

### P1｜熔断与舱壁

- Provider/Vector/Connector 分别设置熔断器与并发上限。
- 不同租户、在线问答、长文生成和 Eval 使用独立队列，避免互相拖垮。
- Ready Check 只反映能否接流量；依赖降级状态通过管理员页面展示。

### P2｜容灾

- PostgreSQL PITR、MinIO 版本/备份、Milvus 重建演练、Redis 非权威缓存定位。
- 定期执行故障演练：Provider 超时、Worker 崩溃、DB 断连、重复 Webhook、审批后进程退出。
- 定义 RPO/RTO 并通过演练验证，而不是只写文档。

## 8.4 预期收益

- 减少永久 pending、重复通知/建单和“假成功”。
- 提高供应商故障时的可用性，同时保持政策问答的证据底线。

## 8.5 依赖

- 依赖 Outbox、Runtime、Model Router、Tool Gateway 和统一观测字段。

---

# 9. 基于 Eval 的自我进化闭环

## 9.1 现状问题

- 已有 250 条 Golden Set、真实 LLM 离线运行和 Agent 轨迹门禁，这是明显优势。
- 数据质量不均：部分场景为模板扩增；周报/文化内容的关键词指标与任务目标不完全匹配。
- 线上 `auto_eval` 仍存在常量占位指标；不能用于自动决策。
- 已有回答反馈，但尚未形成“采样 → 标注 → 回归 → 灰度 → 监控 → 回滚”闭环。
- 缺少检索 Recall@K/MRR、拒答准确率、工具选择、人工接管和业务结果等完整指标。

## 9.2 目标

- 把每次模型、Prompt、检索和工具改动转成可复现评测。
- 只允许通过质量、安全、成本和延迟门禁的版本进入灰度。
- “自我进化”是自动发现问题和生成候选改动，**不是未经人工批准自动上线 Prompt 或工具权限**。

## 9.3 具体改动

### P0｜修复线上评测真实性

- 删除/隔离 0.7/0.5 常量指标；未实现时返回 `not_measured`，不能产生假分。
- Eval Event 通过 Outbox 投递，保存 prompt/model/retrieval/kb/tool 版本和脱敏输入引用。
- 建立数据集分层：golden、candidate、production_failure、red_team、drift。

### P1｜指标体系

**RAG**：Recall@K、MRR、证据覆盖、引用精确率、无证据拒答准确率。
**回答**：Faithfulness、相关性、完整性、可执行性、风险措辞。
**Agent**：任务成功率、工具选择准确率、未授权写率、重复副作用率、审批合规率、人工接管率、平均步骤。
**体验**：首安全句段延迟、任务完成时间、员工确认解决率、HR 修改建议比例、重开率。
**成本**：每个成功任务 Token、模型成本、工具调用成本、评测成本。

### P1｜闭环流程

```
线上运行/反馈/失败
→ 风险脱敏与采样
→ 聚类：检索、Prompt、模型、工具、权限、体验问题
→ 生成候选修复
→ Golden + Red Team + 回放集评测
→ 人工评审差异
→ 5% 租户/流量灰度
→ 指标对比与漂移监控
→ 放量或自动回滚
```

- 用户纠正进入 candidate，经过双人/规则审核后才能进入 golden。
- 高风险 HR 样本必须由具备权限的业务评审确认，不能直接用于模型训练。

### P2｜Champion–Challenger

- 同一请求影子运行 challenger，不向用户展示、不执行工具；比较质量、成本和延迟。
- Prompt/模型/检索策略均保留版本和回滚点。
- 安全指标、未授权写和重复副作用是硬门禁，不能被平均质量分抵消。

### P3｜长期进化

- 自动发现重复失败模式并提出知识缺口、Prompt 变更或工具 Schema 变更建议。
- 允许自动生成 PR/配置候选，但发布仍需人工批准。
- 建立场景级漂移检测与再评测计划。

## 9.4 预期收益

- 防止“凭感觉调 Prompt”，每次升级可量化、可回放、可回滚。
- 将真实用户失败转成持续扩展的数据资产。
- 在不放弃人工治理的前提下提高迭代速度。

## 9.5 依赖

- 依赖 Runtime 轨迹、Context/Prompt/Model 版本、Outbox、反馈 UI、权限和数据脱敏。

---

# 10. 分期路线图

## 短期：0–2 周｜P0 安全与真实性

1. 修复会话上下文名实不符和流式护栏一致性。
2. 引入请求级 Model Router，主链真正接入 fallback，删除全局请求级 Provider 切换。
3. 统一错误分类和 UI 状态。
4. 抽象 Tool Gateway，接通至少 1 个真实通知工具。
5. 在线 Eval 去除占位分数；建立完整版本字段。
6. 建立分段延迟、Token、错误和依赖指标。

**退出条件**：上下文隔离测试通过；用户所见=持久化=评测输入；失败不重复副作用；无假 Eval 分数。

## 短期：第 3–6 周｜P1 端到端业务闭环

1. 新增 Case 列表、详情和审批中心。
2. 增加业务状态、负责人、下一步、等待对象、截止时间和保密范围。
3. Outbox + Celery 可靠投递；Connector 同步后台化。
4. 打通“制度问答 → 请求 → Case → 方案 → 审批 → 执行 → 通知”。
5. 增加 RAG、Agent、体验和成本 Eval Gate。
6. 完成一个企微或飞书真实租户试点。

**退出条件**：一条真实旅程可回放、可恢复、可审计；HR 能明确看到下一步；员工可看到安全的本人状态。

## 中期：2–3 个月｜P2 平台化

1. 发布 MCP 只读能力，再开放受审批的写能力。
2. n8n/企业渠道通过 Tool Gateway 接入。
3. Web/Agent/Ingestion/Eval Worker 分队列与扩缩容。
4. Champion–Challenger、影子流量和自动回滚。
5. SSO、KMS、字段级敏感数据保护和权限即时撤销。

## 长期：3–6 个月｜P3 规模化与生态

1. 根据实测瓶颈拆分高负载服务，而非预先微服务化。
2. 扩展连接器认证和工具 SDK/模板生态。
3. 长流程确有需求时评估 LangGraph；仍保持业务状态机和 Tool Gateway 为权威边界。
4. 建立漂移监控、自动候选修复、受控 Prompt/模型进化。
5. 完成容灾演练、RPO/RTO 验证与企业合规评审。

---

# 11. 模块依赖与关键路径

| 交付项 | 前置依赖 | 被哪些模块依赖 |
| --- | --- | --- |
| 权限即时失效/ACL | Identity、用户模型 | Tool、Context、UI、Eval |
| Tool Gateway | RBAC/ACL、Schema、审计 | Agent Runtime、MCP、n8n、写执行 |
| Outbox/Celery | PostgreSQL、幂等模型 | 审计、通知、Eval、Connector |
| Model Router | Provider 配置、错误分类 | 所有 LLM 场景、容灾、成本治理 |
| Context Manager | 消息/Case、ACL、Token 预算 | 对话、RAG、Prompt、Eval |
| Agent Runtime | Tool Gateway、Outbox、状态模型 | Case UI、轨迹、审批、Eval |
| Case UI | Case API、业务状态、Runtime | HR 日常工作闭环 |
| Eval Gate | 版本信息、轨迹、反馈、脱敏 | Prompt/模型/检索发布 |
| MCP/渠道 | Tool Gateway、身份映射、审计 | 外部生态与员工入口 |

**关键路径**：Identity/ACL → Tool Gateway + Outbox → Model Router + Context → Agent Runtime → Case UI → Eval Gate → MCP/渠道扩展。

# 12. 评审需要重点决策的事项

1. 首个端到端 Case 是否确定为“员工事项受理与持续跟进”，并以绩效沟通支持作为第一条样例旅程？
2. 首个真实执行器选择企微通知、外部工单还是内部状态更新？
3. 首个试点渠道选择企微还是飞书？
4. 哪些高风险类别只允许取证和转人工，禁止生成或执行处理结论？
5. 建议 SLO 是否符合试点规模；预计并发、文档量和租户数仍需补充。
6. Prompt/模型变更的发布审批人和业务评审人分别由谁承担？

<aside>
✅

**建议立项方式**：先批准 6 周 P0/P1 范围，验收一条真实端到端旅程；MCP、多 Agent、微服务和多渠道并行开发均不进入首期承诺。

</aside>
