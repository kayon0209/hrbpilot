# HRBPilot 端到端整体改动方案（v2 可执行版）

> 状态：**工程评审完成，等待 Gate 0 基线恢复后实施**  
> 版本日期：2026-09-04  
> 实施范围：完整保留 P0/P1；P2/P3 只保留接口和边界，不提前建设  
> 当前结论：方案方向可行，但当前工作树和自动化门禁尚未达到可实施基线  
> 适用仓库：`D:\demo\hrbpilot`

---

## 1. 执行摘要

HRBPilot 的目标不是把每个 LLM 调用包装成 Agent，而是把 AI 能力变成可跟踪、可审批、可恢复、可审计、可完结的 HR 工作系统。

P0/P1 的唯一主线是：

```text
员工事项进入
→ HRBP 受理并形成 Case
→ Agent 汇总事实、证据和缺口
→ 人工确认建议方案
→ 高风险动作进入 ApprovalTask
→ Tool Gateway 受控执行
→ 员工和 HR 看到各自可见的状态
→ Case 完成、转人工或重开
→ 轨迹进入 Eval 与改进闭环
```

本方案采用以下工程结论：

1. 使用统一 `RunEnvelope`，但按语义拆分 `GovernedCaseRun`、`GenerationJob`、`ConversationTurn`。
2. Case、AgentRun、ApprovalTask、ToolExecution 使用正交状态，不再共用一个混合状态机。
3. Tool Gateway 是唯一副作用执行内核；旧入口只允许单向委托，禁止新旧双写。
4. 写工具提供 effectively-once，而不是虚假的 exactly-once；外部结果不确定时进入 `UNKNOWN`。
5. 安全审计与业务事务同成同败；关键任务走 Transactional Outbox；在线 Eval 与遥测分级处理。
6. Worker 使用服务端 Execution Grant，并在执行时重新校验当前权限。
7. 暂停、取消、恢复和人工接管使用 Lease、Attempt 和 Fencing Token。
8. ApprovalTask 是独立的人类工作对象，并签发精确、一次性的 Execution Grant。
9. 使用内部类型化 Policy API 和唯一 ToolCatalog，不在 P0/P1 引入通用外部策略引擎。
10. 事件和持久化 payload 必须类型化、版本化并支持旧版本读取。
11. 正常治理结果使用 `Outcome`，真正故障使用 `Failure`。
12. Application Command Handler / Unit of Work 是唯一事务所有者。

### 1.1 成功标准

P1 结束时必须同时满足：

- 一条真实 Case 旅程可回放、可恢复、可审计。
- HR 登录后能看到下一步、等待对象、截止时间和负责人。
- 员工只能看到本人且适合对外展示的状态和更新。
- 写工具未经授权不会执行，授权过期或权限撤销后 fail closed。
- 重复投递、Worker 丢失和请求重试不会造成重复副作用。
- 外部结果不确定时明确显示“待核对”，不伪造成功或失败。
- Prompt、模型、检索、工具、策略和事件 Schema 均可追溯到版本。
- 真实 PostgreSQL/RLS/并发测试与四角色 E2E 没有关键 skip。

### 1.2 不成功的假象

以下情况不能视为完成：

- 类、接口、路由或页面已经存在，但没有真实调用链。
- 单元测试全绿，但 PostgreSQL/RLS/并发/E2E 被跳过。
- Tool 返回模拟成功或没有生产 Executor。
- 页面能打开，但没有真实 Case/Approval 数据和用户下一步。
- Outbox 表已创建，但业务写入和 Outbox 不在同一事务。
- Runtime 有 `CANCELLED` 状态，但旧 Worker 仍能继续回写。
- Eval 输出分数，但无法证明使用了真实 Judge、真实轨迹和明确版本。

---

## 2. 当前基线与实施前置条件

### 2.1 2026-09-04 评审开始时工作树快照

以下数字采集于写入本 v2 之前；本文件新增后，未跟踪文件数量会增加 1。它们用于说明评审基线，不是持续更新的项目状态页。

本节是评审时快照，不是永久事实；每个阶段开始前必须重新验证。

| 项目 | 当前证据 |
|---|---|
| Git | `main @ e096ad5` |
| 工作树 | 141 个变更项：69 modified、72 untracked |
| 已跟踪 diff | 67 个文件，约 `+3780/-452` |
| Ruff | `ruff check app tests` 通过 |
| MyPy | `mypy app` 通过，177 个源文件 |
| 后端聚焦测试 | 135 collected：113 passed、11 failed、10 skipped、1 error |
| 前端 Vitest | 12 个文件、34 个测试通过 |
| 前端 build | 失败：Policy QA SSE 类型缺少 `history_used/history_message_count` |
| Alembic | 单 head：`029_eval_result_is_stub`；current/check 未在可用隔离库验证 |
| PostgreSQL/RLS/并发 | 本轮未获得激活态证据；关键测试被环境门控跳过 |
| 四角色 Playwright | 本轮未执行 |
| 性能 | 只有 Locust/k6 骨架，没有正式基线 |

### 2.2 已存在或部分存在

- HRCase、CasePlan、ApprovalRequest、ToolExecution、CaseEvent、AgentRun 六类模型。
- 有限步数、工具白名单、写操作审批、审批和执行分离的 Agent Loop。
- 请求级 Model Router 和 Context Manager 的工作树实现。
- 在线 Eval 的失败即 skip 语义和历史 stub 隔离。
- Celery `acks_late`、worker-lost 重投递、prefetch 和超时设置。
- PostgreSQL 审批并发、事件序列、RLS 和真实 HTTP 验收测试文件。
- Connector/OAuth/Webhook/同步租约的工作树实现。
- Locust/k6 性能测试骨架。
- React/Vite/Vitest/Playwright 测试基础。

### 2.3 尚未完成

- 唯一 Tool Gateway 内核和版本化 ToolCatalog。
- Transactional Outbox、Dispatcher、DLQ 重放边界。
- 三种 Execution Profile 和 Runtime Coordinator。
- Lease、Attempt、Heartbeat、Fencing Token。
- 独立 ApprovalTask 和服务端 Execution Grant。
- `UNKNOWN` 外部执行结果、Provider 查询和人工对账。
- `/cases`、`/cases/:id`、`/approvals` 页面与真实 API。
- 生产 Tool Executor 的真实注册与一个真实租户验证。
- 可作为发布门禁的 PostgreSQL/RLS/并发、四角色 E2E 和性能基线。

### 2.4 Gate 0：开始新架构代码前必须完成

1. 给当前 141 个工作树变更建立归属和验证清单，禁止 reset、stash、clean 或覆盖不明变更。
2. 修复当前 11 个后端失败、1 个错误和前端 build。
3. 在隔离 PostgreSQL 上验证 Alembic `current/head/check` 和目录对象。
4. 激活 `HRBP_RUN_CONCURRENCY_TESTS=true` 与 `HRBP_RUN_DB_SECURITY_TESTS=true`，关键 skip 为 0。
5. 确认测试角色为 `NOSUPERUSER NOBYPASSRLS`；迁移角色与测试角色分离。
6. 重新标注本方案每个任务为 `DONE / PARTIAL / TODO / BLOCKED`，避免重复开发。

**Gate 0 未通过时，不得创建新 Runtime/Outbox Schema，也不得宣布 P0 开始。**

---

## 3. 目标分层架构

```text
L6 运营与进化       Eval Registry / Feedback / Drift / Release Gate
                         ↑
L5 体验与渠道         Employee Portal / HR Workbench / Manager / Admin / Channel
                         ↑
L4 业务投影与服务     Request / Case / Approval / WorkTask / Knowledge / Report
                         ↑
L3 Agent 运行层       RunEnvelope / Profiles / Coordinator / Context / Model Router
                         ↑
L2 受控执行层         Tool Gateway / Policy / Grant / Idempotency / Outbox / Connector
                         ↑
L1 应用事务层         Command Handler / Unit of Work / Event / Projection
                         ↑
L0 信任与数据层       Identity / RBAC+ACL / Tenant / PostgreSQL / Redis / Milvus / MinIO
```

固定依赖规则：

1. UI 只调用业务 API，不直接调用模型、Runtime 或 Connector。
2. Agent/Profile 不直接访问数据库和 HTTP Client，只调用应用服务和 Tool Gateway。
3. Tool Gateway 不信任调用方提供的 tenant、role、scope 或 approver。
4. Domain Service/Repository 不执行 commit/rollback。
5. 外部 I/O 不放在数据库事务中。
6. Outbox 与触发它的业务修改必须在同一个 Unit of Work 中写入。
7. PostgreSQL 是业务状态权威；Redis、Celery Backend、Milvus 和日志均不是业务权威。
8. 模块化单体保持到出现明确独立扩缩容、故障域或发布节奏需求为止。

---

## 4. RunEnvelope 与执行 Profile

### 4.1 公共 RunEnvelope

```text
RunEnvelope
- run_id / tenant_id / scenario_id / profile
- initiated_by / execution_grant_id
- correlation_id / causation_id
- desired_state / observed_state
- attempt_no / fencing_token
- lease_owner / lease_expires_at / heartbeat_at
- prompt_version / model_route_version / retrieval_version
- policy_version / tool_catalog_version / event_schema_version
- started_at / finished_at
- token_usage / cost / segment_latency
- outcome_kind / failure_code / handoff_reason
```

公共层只统一身份、版本、轨迹、成本、生命周期和错误，不要求每个 Run 都有 Plan、Step、Tool 和 Approval。

### 4.2 GovernedCaseRun

适用于 HR Case 等需要事实收集、计划、审批、写执行和人工接管的长流程。

```text
Case Context
→ Evidence Collection
→ Plan Draft
→ Human Review
→ ApprovalTask（按需）
→ ToolExecution（按需）
→ Case Projection
→ Completed / HandedOff / Failed
```

约束：

- 每个 Run 有最大步骤、最大 Token、总 Deadline 和最大工具尝试次数。
- 高风险类别只能取证、总结和转人工，不得生成最终用工决定或执行写工具。
- Runtime 负责调度和观测，业务状态只能通过 Case Command 修改。

### 4.3 GenerationJob

适用于周报、文化内容、面谈摘要和语音洞察。

```text
InputRef
→ Fixed Pipeline Stages
→ Artifact Draft
→ Validation/Guardrail
→ Persisted Artifact
→ Completed / Partial / Failed
```

不强制创建虚假的 Plan、Approval 或 ToolExecution；只有真实调用 Tool Gateway 时才产生相应记录。

### 4.4 ConversationTurn

适用于制度问答等流式对话。

```text
Current User Message
→ Authorized Context
→ Retrieval Evidence
→ Safe Stream
→ Persist Visible Answer
→ Eval Event
```

必须保证：用户所见内容、持久化内容和 Eval 输入一致；断开连接、护栏改写和网络错误均不得留下不可解释的尾部内容。

---

## 5. 正交状态与用户投影

### 5.1 Case 生命周期

```text
OPEN → RESOLVED → CLOSED
  ↑        │         │
  └────────┴─ REOPENED
```

Case 另存或计算：

- `owner_id`
- `next_action`
- `waiting_on: NONE | EMPLOYEE | HRBP | MANAGER | EXTERNAL`
- `due_at`
- `confidentiality_level`
- `participant_scope`
- `resolution_summary`

### 5.2 AgentRun 状态

```text
desired_state: RUNNING | PAUSED | CANCELLED | HUMAN_TAKEOVER
observed_state: QUEUED | RUNNING | PAUSED | COMPLETED | FAILED | HANDED_OFF | CANCELLED
```

### 5.3 ApprovalTask 状态

```text
PENDING → APPROVED → CONSUMED
   ├────→ REJECTED
   ├────→ EXPIRED
   └────→ REVOKED
```

### 5.4 ToolExecution 状态

```text
PENDING → CLAIMED → RUNNING → SUCCEEDED
                         ├──→ FAILED
                         └──→ UNKNOWN → RECONCILED_SUCCEEDED / RECONCILED_FAILED
```

### 5.5 UI 业务状态

“待受理、待补充、处理中、待审批、待确认、已完成、已转人工”由服务端 Projection 根据 Case、Run、Approval 和 ToolExecution 组合生成，前端不得自己推断多表状态。

---

## 6. 身份、权限与执行授权

### 6.1 服务端 Execution Grant

入口完成认证和外部身份映射后，由服务端创建受限 Grant；队列只传 `run_id / execution_id / grant_id`。

```text
ExecutionGrant
- tenant_id
- subject_type / subject_id
- capability
- object_type / object_id
- policy_version / permissions_version
- allowed_tool / allowed_action
- normalized_params_hash（写工具）
- issued_at / expires_at
- issued_for_run_id
- max_consumptions
```

执行规则：

- 读取数据时重新计算当前 ACL。
- 写工具 Claim 前重新验证用户仍有效、权限版本未撤销、对象范围仍匹配。
- Approval 不覆盖发起人已被撤销的权限。
- 系统任务使用显式 Service Principal，禁止使用隐式 `system` 绕过策略。
- 已经发送到外部系统的副作用无法通过撤权伪装撤回，只能记录并对账。

### 6.2 ToolCatalog 与 Policy API

```text
ToolDefinition
- name / version
- input_schema / output_schema
- kind: read | write
- required_capability
- risk_level
- approval_policy
- timeout / retry_policy
- idempotency_capability
- reconciliation_capability

PolicyDecision
- allowed
- reason_code
- policy_version
- evaluated_subject
- evaluated_object
- constraints
```

Planner、Approval 创建和真正执行都重复调用同一 ToolCatalog/Policy；允许重复校验，不允许重复定义规则。

平台管理员默认没有 HR 业务权限。审批资格由 Approval Policy 和对象范围决定，不由 `admin` 身份自动继承。

---

## 7. ApprovalTask：人类工作与一次性授权

```text
ApprovalTask
- initiated_by_user_id / initiated_by_run_id
- immutable tool_name / tool_version
- normalized_params_hash / target_object
- risk_snapshot
- policy_id / policy_version
- eligible_approver_rule
- assigned_user_id / assigned_group
- separation_of_duties
- required_approval_count（P1 固定为 1）
- due_at / expires_at / escalation_level
- status / decided_by / decision_reason / decided_at
```

P1 范围：

- 只实现单人单级审批。
- 支持分派给明确用户或受控角色组。
- 发起人与审批人冲突时 fail closed。
- Case Owner、风险或参数发生实质变化时撤销旧审批并重新申请。
- 审批页面展示动作、影响对象、参数差异、依据、风险和过期时间。
- 批准后签发一次性、精确绑定 Tool/版本/对象/参数哈希的 Execution Grant。

代理、会签、多级审批和企业 BPM 集成不进入 P1。

---

## 8. Tool Gateway 与 effectively-once

### 8.1 唯一执行入口

```text
Tool Call
→ Schema Validation
→ Load Trusted ExecutionGrant
→ Capability + Object ACL + Tenant
→ Risk Policy + SoD
→ Approval/Grant Validation
→ Params Hash
→ Durable Claim
→ Outbox Dispatch
→ External Execution
→ Result / UNKNOWN
→ Event + Projection
```

旧的 `TOOL_SCHEMAS/TOOL_KINDS/register_tool_executor` 通过兼容适配器单向委托给新 Gateway；新 Gateway 不回调旧执行内核。

### 8.2 外部副作用语义

跨 PostgreSQL 和第三方系统不承诺通用 exactly-once。目标是：

- 下游支持幂等键和查询：effectively-once。
- 下游不支持查询但支持幂等键：幂等重试。
- 下游既不支持幂等也不支持查询：最多尝试一次，结果不确定时人工核对。

```text
ClaimToolExecutionCommand（事务 1）
  consume grant
  create ToolExecution=PENDING
  create ToolDispatch Outbox
  commit

Worker（事务外）
  call downstream with stable external_idempotency_key

CompleteToolExecutionCommand（事务 2）
  verify fencing token
  write SUCCEEDED / FAILED / UNKNOWN
  append event
  update projection
  commit
```

超时、断连和 Worker 丢失不得直接标记为 `FAILED`；只有确定性失败才是 `FAILED`。`UNKNOWN` 先查询下游、对账或转人工，禁止盲目重试高风险写操作。

---

## 9. Outbox、队列与可靠任务

### 9.1 交付等级

| 事件 | 交付方式 | 优先级 |
|---|---|---|
| 权限修改、审批决定、安全业务写入的审计 | 与业务事务同步写审计表 | 最高 |
| Tool Dispatch、通知、Connector 同步 | Transactional Outbox | 高 |
| GenerationJob 启动 | Transactional Outbox | 高 |
| 在线 Eval | 独立低优先级队列，可补跑 | 低 |
| 延迟、Token、普通遥测 | 日志/指标系统，best effort | 低 |
| 发布门禁 Eval | 独立离线 Eval Run | 发布阻断 |

Outbox payload 只保存路由信息、版本、哈希和受控对象引用；禁止复制完整员工材料、Prompt、文档或模型输出。

### 9.2 Dispatcher 要求

- `SELECT ... FOR UPDATE SKIP LOCKED` 或等价 Claim。
- 稳定 event ID、dedupe key、attempt、lease、next_attempt_at。
- 指数退避加抖动，区分 retryable 与 terminal。
- DLQ 重放必须重新校验当前权限和工具策略。
- 关键工具、通知、生成任务、Connector、Eval 使用独立队列。
- Dispatcher 重复投递是正常情况，Consumer 必须幂等。

---

## 10. Runtime Coordinator：暂停、取消与接管

Runtime 使用数据库协调，不以 Celery revoke/terminate 作为正确性保证。

```text
Worker Claim
  UPDATE run
  SET lease_owner=?,
      lease_expires_at=?,
      attempt_no=attempt_no+1,
      fencing_token=fencing_token+1
  WHERE lease 已过期 AND desired_state=RUNNING
```

规则：

- Worker 在检索、模型调用、每个 Step 和 Tool Claim 前后检查 `desired_state`。
- 每次结果回写必须携带当前 fencing token；旧 token 的 Worker 不得写 Run、Artifact、Plan 或 Projection。
- 暂停只保证在定义好的安全点生效，不承诺任意指令中途冻结。
- Resume 创建新 Attempt 或领取新租约，不复用结果未知的旧上下文。
- Human Takeover 改变 `desired_state` 并使当前租约失效。
- 外部副作用已经发出时，取消不假装撤销，转入完成记录或 `UNKNOWN` 对账。
- UI 区分“正在请求取消”与“已取消”。

---

## 11. 应用事务、事件与错误契约

### 11.1 Unit of Work

```text
Route / Worker Entry
→ Application Command Handler
→ Unit of Work
   ├─ Domain Service
   ├─ Repository
   ├─ Security Audit
   └─ Outbox Writer
→ 单一 commit / rollback
```

禁止事项：

- Domain Service 和 Repository 内部 `commit/rollback`。
- Route 直接修改 ORM 模型。
- 在数据库事务中调用 LLM、Milvus、MinIO 或外部 Connector。
- 为保存异常状态在 Service 中临时 commit 后再抛异常。

### 11.2 版本化事件

```text
RunEvent
- event_id / tenant_id / run_id / case_id
- event_type / schema_version
- occurred_at
- actor_type / actor_id
- correlation_id / causation_id
- tool_version / prompt_version / model_version / policy_version
- protected_payload_ref
- typed payload
```

- 每个 payload 使用显式 Pydantic 模型，禁止持久化裸 `dict`。
- `event_type + schema_version` 唯一确定 payload 类型。
- 旧事件通过版本 reader/upcaster 读取，不原地改写历史事件。
- tenant、run、case、状态、时间和版本等查询字段使用标准列。
- API DTO 与数据库事件模型分离。

### 11.3 Outcome 与 Failure

```text
Outcome
  COMPLETED / NO_EVIDENCE / POLICY_BLOCKED / PARTIAL /
  AWAITING_APPROVAL / HANDED_OFF / CANCELLED / EXTERNAL_OUTCOME_UNKNOWN

Failure
  VALIDATION / AUTHENTICATION / PERMISSION /
  DEPENDENCY_TIMEOUT / RATE_LIMIT / DEPENDENCY_UNAVAILABLE /
  MODEL_BAD_OUTPUT / CONTEXT_OVERFLOW / TOOL_FAILURE /
  IDEMPOTENCY_CONFLICT / INTERNAL_BUG
```

`NO_EVIDENCE`、策略阻断、人工拒绝、部分完成和等待审批是受控 Outcome，不进入系统故障率。`Failure` 通过集中 ErrorPolicy 映射重试、HTTP 状态、告警级别、安全文案和恢复动作。

---

## 12. UI 与首条业务闭环

### 12.1 页面

- `/cases`：风险、业务状态、下一步、等待对象、负责人、计划时间、最近更新。
- `/cases/:id`：事实、材料、参与人、时间线、结果；右侧展示 AI 摘要、证据、缺口和建议方案。
- `/approvals`：仅展示当前用户有资格处理的 ApprovalTask。
- 员工入口：只显示本人请求、适合对外展示的状态和明确下一步。
- 管理员后台：与 HR 业务工作台分离；管理员不自动获得 Case 和 Approval 权限。

### 12.2 首条垂直旅程

```text
员工提交事项
→ HRBP 受理
→ 创建 Case
→ Agent 汇总事实/证据/缺口
→ HRBP 补充并确认方案
→ 创建内部 WorkTask 或更新 Case next_action
→ 必要时 HR 经理审批
→ Tool Gateway 执行
→ 员工看到安全状态更新
→ Case 完成或重开
```

第一个写工具优先选择 `create_work_task` 或 `update_case_next_action`：可逆、可审计、立即形成用户闭环。第二个写工具再接真实企微通知，并验证身份映射、幂等和对账。

绩效沟通只作为示例材料，不允许 Agent 自动形成绩效结论、纪律处分或用工决定。

### 12.3 流式一致性

- 持久化内容必须等于用户实际看到的最终安全内容。
- 浏览器断开后停止消费上游流、释放并发额度，并记录受控 Outcome。
- 护栏替换、停止生成和网络中断后的尾部文本不得进入历史或 Eval。
- 前端不解析数据库事件推断业务状态，只消费服务端 Projection DTO。

---

## 13. Model Router、Context 与 Prompt

### 13.1 Model Router

- 每次请求使用不可变 `ModelRequest`；禁止修改全局 active provider。
- 路由输入包括 scenario、risk、latency、context size、tenant policy 和 cost tier。
- 重试只覆盖超时、连接中断和明确可重试 5xx。
- 429 遵循 Retry-After 或切换 Provider；4xx 参数错误不重试。
- 所有尝试受请求总 Deadline 约束，fallback 不重新获得完整超时预算。
- Provider Registry 和 Client Cache 必须具备测试隔离与并发安全性。

### 13.2 Context Manager

上下文顺序：

```text
System Policy
→ Scenario Task
→ Authorized Case/Session Summary
→ Retrieved Evidence（不可信数据）
→ Tool Results
→ Current User Message
```

- 历史摘要可追溯到 message ID；用户纠正后旧摘要失效。
- 检索只引入必要指代，不把完整聊天历史塞入 Query。
- Context 在加载数据时重新执行当前 ACL。
- 不同 tenant、员工、Case、Session 严格隔离。
- 文档和聊天内容永远是数据，不得获得 system instruction 权限。

### 13.3 Prompt Registry

```text
PromptSpec
- name / version / scenario / risk
- output_schema
- allowed_tools
- eval_suite
- status
- created_by / approved_by
- created_at / activated_at
```

Prompt 变更必须附差异、回归评测和回滚点。自动化只能生成候选，不得未经人工批准自动发布 Prompt、模型路由或工具权限。

---

## 14. Eval 与持续改进

### 14.1 真实性原则

- 未实现或 Judge 失败的指标返回 `not_measured/skipped`，不写占位分数。
- Eval Event 保存 run、prompt、model、retrieval、KB、tool、policy 和 event schema 版本。
- 输入使用脱敏引用，不复制原始 HR 数据到 Eval 队列。
- 安全、未授权写和重复副作用是硬门禁，不能被平均质量分抵消。

### 14.2 指标

| 类型 | 指标 |
|---|---|
| RAG | Recall@K、MRR、证据覆盖、引用精确率、无证据拒答准确率 |
| 回答 | Faithfulness、相关性、完整性、可执行性、风险措辞 |
| Agent | 任务成功率、工具选择准确率、未授权写率、重复副作用率、审批合规率、人工接管率 |
| 体验 | 首安全句段延迟、任务完成时间、员工确认解决率、HR 修改比例、重开率 |
| 成本 | 每个成功任务 Token、模型成本、工具成本、Eval 成本 |

### 14.3 闭环

```text
线上运行/反馈/失败
→ 权限过滤、脱敏与采样
→ 失败分类
→ Candidate
→ Golden + Red Team + Replay
→ 人工业务评审
→ 小流量灰度
→ 指标与漂移监控
→ 放量或回滚
```

用户纠正进入 Candidate；高风险样本必须由有权限的 HR 业务评审确认，不能直接进入 Golden 或训练集。

---

## 15. 性能与容量

### 15.1 候选 SLO

以下仅为待验证目标，不是当前能力声明：

| 链路 | 候选目标 |
|---|---|
| 非 LLM 关键 API p95 | ≤ 300 ms |
| Case 列表 p95 | ≤ 500 ms |
| Hybrid Retrieval p95 | ≤ 800 ms |
| 制度问答首安全句段 p95 | ≤ 2.5 s |
| 制度问答完整回答 p95 | ≤ 12 s |
| 异步任务受理 p95 | ≤ 500 ms |

每个 SLO 必须同时记录：commit、机器、数据规模、tenant/KB 数量、并发、到达率、时长、样本数、缓存冷暖、Provider/模型、依赖版本、成功率、超时率和是否包含排队时间。

写工具“审批后执行成功率”属于可靠性 SLI，必须拆分为平台受理/派发成功率、下游明确拒绝率、`UNKNOWN` 率和最终对账成功率。

### 15.2 性能策略

- 使用总 Deadline 分配检索、Primary、Fallback 和 Finalize 预算。
- Query Rewrite 和 Embedding 可缓存。
- 检索缓存只保存候选 ID/分数，每次请求按当前 ACL 重新过滤和加载内容。
- 高敏感 Case 上下文和最终回答默认不跨用户缓存。
- 在线问答、关键工具、生成任务、Connector、Ingestion 和 Eval 使用独立队列/信号量。
- SSE 实现背压、断开检测、上游取消和租户级最大并发。
- 优化前先记录基线，不用建议 SLO 反向证明优化成功。

---

## 16. 模块与目录约定

新目录在实施前按下表建立；目录职责不得交叉。

| 模块 | 建议目录 | 只能负责 |
|---|---|---|
| Runtime contracts/coordinator | `app/runtime/` | RunEnvelope、Profile、Lease/Fencing、运行事件 |
| Tool catalog/gateway | `app/tools/` | ToolDefinition、Policy 调用、Claim、Connector 调度 |
| Approval application | `app/approvals/` | ApprovalTask、SoD、分派、Decision、Execution Grant |
| Outbox | `app/outbox/` | 消息持久化、Claim、Dispatcher、DLQ |
| Application commands/UoW | `app/application/` | 事务边界和跨领域命令编排 |
| Domain policy | `app/access/policies/` | 类型化能力、对象范围和领域策略 |
| Scenario adapters | `app/scenarios/<scenario>/` | 场景输入输出和 Profile 适配，不拥有公共内核 |
| API routes | `app/access/routes/` | 认证后的 DTO、Command 调用、HTTP 映射 |
| ORM models | `app/data/models/` | 数据映射，不放业务编排 |
| Frontend feature | `web/src/features/` | 业务页面与组件，状态来自 API Projection |

禁止新增万能 `shared/utils.py`、`agent_service.py` 或同时管理 Case/Approval/Tool/Outbox 的 God Service。

复杂状态文件必须包含紧邻代码的 ASCII 状态图；Runtime Coordinator、Outbox Dispatcher 和 Tool Gateway 必须包含事务/失败窗口图。

---

## 17. 绞杀式迁移

```text
阶段 1：冻结现有行为
  approval / idempotency / ACL / failure contract tests

阶段 2：建立新内核
  ToolCatalog + Policy + UoW + Approval + Gateway

阶段 3：旧接口变为兼容适配器
  validate_tool_call / run_plan / execute_approved_write
  只允许单向调用新内核

阶段 4：逐个切换调用者
  HRCase Route → Planner → Agent Loop → Tests

阶段 5：全仓确认零调用后移除旧全局 Registry
```

迁移约束：

- 不允许审批、ToolExecution 或 Outbox 双写。
- 不允许旧 `TOOL_EXECUTORS` 与新 Connector Registry 长期并存。
- 每一步运行同一组安全 Contract Tests。
- 第一个迁移对象是只读工具；第二个是内部可逆写工具。
- 数据库迁移由单一 Schema Owner 串行创建；revision 从实施时实际 Alembic head 派生，不预先猜编号。
- 任何 Schema 变更或数据迁移实施前必须单独获得授权，并只在已证明隔离的测试库执行验证。

---

## 18. 测试矩阵与 Phase Gate

### 18.1 测试拓扑

```text
Auth / Identity Mapping
→ Execution Grant / Permission Revocation
→ Command + Unit of Work
→ Business + Audit + Outbox Atomic Commit
→ Duplicate Dispatch / Worker Loss
→ Lease + Fencing
→ Execution Profile
→ Approval / SoD / Expiry
→ Tool Claim
→ Connector Success / Failure / UNKNOWN
→ Versioned Event / Projection
→ Case UI / Approval UI / Eval
```

### 18.2 必测场景

| 边界 | 正常 | 异常/边界 | 必须使用真实依赖 |
|---|---|---|---|
| Policy/ACL | 当前用户可见 | 跨 tenant、跨对象、撤权、旧 Grant | PostgreSQL 非特权角色 |
| Approval | 合格审批人批准/拒绝 | SoD、过期、并发批准/拒绝、对象变化 | PostgreSQL 并发 |
| Outbox | 提交后投递 | commit 前崩溃、commit 后崩溃、重复投递、DLQ | PostgreSQL + Worker |
| Fencing | 单 Worker 完成 | 旧 Worker 恢复、续租失败、取消/接管竞态 | PostgreSQL 并发 |
| Tool | 成功/明确失败 | 参数变更、重复 request、响应丢失、UNKNOWN 对账 | 受控假下游 + 真实首个 Connector |
| Events | 当前版本读取 | v1→v2 upcast、未知版本 fail closed | PostgreSQL |
| Conversation | 正常流式 | 断开、护栏改写、Provider fallback | 真实 SSE；Provider 可控故障 |
| Generation | 完整制品 | partial、重试、Worker 丢失 | Worker + 对象存储按阶段要求 |
| UI | 四角色正确旅程 | 越权 URL、空态、失败恢复、移动端 | 真实前后端和账号 |
| Eval | 可复现评分 | Judge 失败、not_measured、版本缺失 | 离线 Eval Run |

### 18.3 本地/CI 命令门禁

```powershell
.\.venv\Scripts\python.exe -m ruff check app tests evaluation
.\.venv\Scripts\python.exe -m ruff format --check app tests
.\.venv\Scripts\python.exe -m mypy app
.\.venv\Scripts\python.exe -m pytest -q

corepack pnpm --dir web lint
corepack pnpm --dir web test:run
corepack pnpm --dir web build
```

数据库门禁必须在隔离 PostgreSQL 上设置：

```powershell
$env:HRBP_RUN_CONCURRENCY_TESTS='true'
$env:HRBP_RUN_DB_SECURITY_TESTS='true'
.\.venv\Scripts\python.exe -m pytest tests/hr_case tests/connectors tests/shared -q
```

验收规则：

- 命令退出码非 0 即失败。
- 输出截断、测试仍在运行或缺少最终汇总，不算通过。
- 关键 skip 非 0，不算通过。
- SQLite 单测不能替代 PostgreSQL 并发、锁、RLS 和迁移证据。
- `playwright --list`、mock 页面和缺账号 skip 不能替代真实四角色 E2E。
- 性能脚本可运行不等于 SLO 已达成。

### 18.4 当前必须补齐的 Critical Gaps

1. 权限批准后、Worker 执行前被撤销。
2. 外部操作成功但响应丢失后的 `UNKNOWN + 对账`。
3. 取消/人工接管后旧 Worker 恢复并尝试回写。
4. 缓存命中后 ACL、保密范围或 KB 版本已经变化。

---

## 19. 分期路线图

完整 P0/P1 保留，但不再承诺单人 6 周必然完成。当前估算：单人 + Codex 为 8–12 周；若基线稳定且三条 Lane 并行，可争取 6–8 周。

### Wave 0｜2–4 天：恢复可信基线

- 完成 Gate 0。
- 修复后端、前端 build 和测试隔离问题。
- 重建当前能力矩阵。

退出条件：全部普通门禁绿色；真实数据库门禁环境可运行；变更归属清楚。

### Wave 1｜1–2 周：冻结契约

- RunEnvelope 与三种 Profile。
- 正交状态和 Projection DTO。
- ToolCatalog、PolicyDecision、Outcome/Failure。
- Versioned Event。
- Command/UoW。
- Contract Tests。

退出条件：不依赖具体 Connector 的契约测试全绿；Schema 方案通过单独审批。

### Wave 2｜2–4 周：可信执行内核

- ApprovalTask 与 Execution Grant。
- Tool Gateway。
- Outbox/Dispatcher/DLQ。
- Runtime Lease/Fencing。
- UNKNOWN 与对账。
- 通过兼容适配器接管 HRCase。

退出条件：重复投递、Worker 丢失、并发审批、撤权和未知外部结果测试全绿；旧执行内核不再承担真实副作用。

### Wave 3｜2–3 周：Case 产品闭环

- Case API、Projection、列表、详情和 Approval Center。
- 员工状态和安全更新。
- 首个内部可逆写工具。
- 制度问答/员工请求转 Case。

退出条件：四角色真实旅程通过；HR 和员工都能看到明确下一步；Case 可完成和重开。

### Wave 4｜1–2 周：真实试点与 Gate

- 第二个真实外部写工具，优先企微通知。
- Eval provenance、回放和发布门禁。
- 故障注入。
- 性能/容量基线。

退出条件：一个真实租户完成受控旅程；外部副作用可对账；建议 SLO 得到实测结论。

---

## 20. 并行实施策略

| Lane | 内容 | 依赖 | 主要目录 |
|---|---|---|---|
| A | Contracts、State、Policy、UoW、Event | Wave 0 | `app/runtime/`、`app/application/`、`app/access/policies/` |
| B | Model Router、Context、Outcome/Failure 稳定化 | Wave 0，可与 A 并行 | `app/rag/llm/`、`app/scenarios/policy_qa/` |
| C | Case/Approval UI，使用冻结 DTO 和 mock server | Wave 1，可与 D 并行 | `web/src/features/`、`web/src/api/` |
| D | Approval、Gateway、Outbox、Coordinator | Wave 1；内部顺序执行 | `app/approvals/`、`app/tools/`、`app/outbox/`、`app/runtime/` |
| E | Eval、CI、故障注入、性能 | Event/Run 契约冻结后 | `app/evaluation/`、`tests/`、`.github/` |

冲突约束：

- Lane A 与 D 都可能触及 ORM 和迁移，必须由一个 Schema Owner 串行处理。
- Lane C 不得自行发明 API 字段；DTO 变更先更新共享契约和 contract tests。
- Lane E 的 Eval 不得在事件 Schema 未冻结前绑定临时 payload。
- 不同工作树合并前分别运行本 Lane 测试；合并后运行全量 Gate。

---

## 21. 风险与故障模式

| 故障 | 预防/检测 | 用户看到 | 恢复 |
|---|---|---|---|
| Outbox 重复投递 | Consumer 幂等、唯一 dedupe key | 无重复结果 | 返回既有执行 |
| DB commit 后 Worker 未收到 | Dispatcher 扫描未投递记录 | 已受理/排队中 | 自动重新投递 |
| 外部成功但响应丢失 | `UNKNOWN`、下游查询、external ref | 可能已执行，待核对 | 自动或人工对账 |
| 旧 Worker 恢复 | Fencing token 拒绝旧写入 | 无状态倒退 | 新 Attempt 继续 |
| 权限被撤销 | 执行时重授权、permissions_version | 权限已变化，任务停止 | 重新授权/转人工 |
| Approval 过期或对象变化 | 版本/哈希/expiry 校验 | 需要重新审批 | 创建新 ApprovalTask |
| Provider 超时 | 总 Deadline、fallback、熔断 | 证据可见/稍后重试/转人工 | 新 Run 或人工处理 |
| SSE 断开 | disconnect 检测、上游取消 | 已停止生成 | 不保存未见尾部 |
| Eval 不可用 | 独立队列、not_measured | 主业务不受阻 | 后台补跑 |
| 缓存 ACL 过期 | 命中后重做当前 ACL | 不泄漏数据 | 失效并重新检索 |

---

## 22. Implementation Tasks

- [ ] **T1（P1，human 2–4 天 / Codex 1 天）— Baseline — 恢复当前代码与测试可信基线**
  - 来源：§2 Gate 0
  - 范围：当前工作树、后端测试、前端 build、隔离 PostgreSQL
  - 验证：§18.3 全部门禁，关键 skip=0

- [ ] **T2（P1，human 4–7 天 / Codex 2–3 天）— Contracts — 建立 Run/Event/Outcome/Policy/UoW 契约**
  - 来源：§4、§5、§6、§11
  - 范围：`app/runtime/`、`app/application/`、`app/access/policies/`
  - 验证：Profile、状态、事件版本、事务 contract tests

- [ ] **T3（P1，human 3–5 天 / Codex 1–2 天）— Schema — 设计并迁移正交状态、Approval、Grant、Outbox**
  - 来源：§5、§7、§9
  - 范围：`app/data/models/`、`app/data/migrations/`
  - 验证：隔离 PostgreSQL upgrade/downgrade/check 与目录断言

- [ ] **T4（P1，human 5–8 天 / Codex 2–4 天）— Execution — 实现唯一 Tool Gateway 和绞杀式迁移**
  - 来源：§6、§8、§17
  - 范围：`app/tools/`、`app/scenarios/hr_case_agent/`
  - 验证：ACL、审批、幂等、参数绑定、无旧生产执行路径

- [ ] **T5（P1，human 5–8 天 / Codex 2–4 天）— Reliability — 实现 Outbox、Lease/Fencing、UNKNOWN 对账**
  - 来源：§8、§9、§10
  - 范围：`app/outbox/`、`app/runtime/`、Worker
  - 验证：崩溃窗口、重复投递、旧 Worker、对账测试

- [ ] **T6（P1，human 7–10 天 / Codex 3–5 天）— Product — 完成 Case/Approval/Employee 垂直闭环**
  - 来源：§12
  - 范围：Case API、Projection、`web/src/features/`、`web/src/api/`
  - 验证：真实四角色 E2E

- [ ] **T7（P1，human 3–5 天 / Codex 1–2 天）— CI — 建立真实 PostgreSQL/Redis 安全与并发 Gate**
  - 来源：§18
  - 范围：`.github/workflows/`、测试 fixtures、服务拓扑
  - 验证：非特权角色、关键 skip=0、迁移与 RLS 断言

- [ ] **T8（P2，human 4–7 天 / Codex 2–3 天）— Eval/Performance — 建立回放、发布门禁和性能基线**
  - 来源：§14、§15
  - 范围：`app/evaluation/`、`tests/performance/`
  - 验证：版本齐全、硬安全门禁、可复现实测报告

- [ ] **T9（P2，human 5–10 天 / Codex 2–4 天）— Pilot — 验证首个真实企业 Connector**
  - 来源：Wave 4
  - 范围：Connector、身份映射、幂等、对账、Runbook
  - 验证：真实测试租户受控旅程和故障演练

---

## 23. 不进入 P0/P1

- 多 Agent 自主协作。
- LangGraph 重构。
- MCP 对外发布。
- 多企业渠道同时接入。
- 微服务拆分。
- 开放式个人长期记忆。
- 自动发布 Prompt、模型路由或工具权限。
- Champion–Challenger 自动放量。
- 企业 BPM、多级会签和复杂代理审批。
- 完整 SSO、KMS/WORM 平台化；P1 只冻结数据分类、密钥接口和最小保护要求。

---

## 24. 仍需业务负责人确认

工程默认值已给出，但以下内容不能由代码自行决定：

1. 高风险类别清单：当前至少包括 termination、harassment、discrimination、labor_arbitration；是否扩展到薪酬、医疗、纪律处分等由 HR/法务确认。
2. 首个真实外部执行器：默认在内部 WorkTask 闭环通过后选择企微通知。
3. 首个试点渠道和测试租户：默认企微，但必须有真实测试企业、账号和身份映射。
4. Prompt/模型发布审批人：至少包含技术 Owner 与有权限的 HR 业务评审人；高风险变更需额外安全/隐私评审。
5. SLO 负载模型：并发、数据量、租户数、Provider 和试点规模必须在性能基线前填写。

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|---|---|---|---:|---|---|
| CEO Review | — | Scope & strategy | 0 | NOT RUN | 完整 P0/P1 已由用户确认保留 |
| Outside Voice | — | Independent second opinion | 0 | SKIPPED | 用户要求由当前评审者自行完成剩余审查 |
| Eng Review | `plan-eng-review` | Architecture, code, tests, performance | 1 | ISSUES OPEN | 8 个架构决策、5 个代码质量决策；4 个 Critical Gap；当前基线红灯 |
| Design Review | — | UI/UX gaps | 0 | RECOMMENDED LATER | Case/Approval 页面实现后执行 |

- **VERDICT：** 方案架构有条件通过；Gate 0、Schema 审批、真实数据库门禁和业务负责人确认完成前，不得进入发布实施状态。
- **UNRESOLVED DECISIONS：** 高风险类别最终清单、真实企微试点租户、Prompt/模型审批责任人、SLO 负载模型。
