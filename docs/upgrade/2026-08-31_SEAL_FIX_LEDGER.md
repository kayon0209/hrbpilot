# HRBPilot 封口修复问题台账

开始时间：2026-08-31（本窗口）
分支：main，HEAD: b417a218669414d2402fd7ad123e1c0be8c5dab3
现场：工作树含大量未提交改动（不 reset/stash/checkout/clean/delete/commit/push）
数据库现场：alembic current=018，heads=019（alembic check FAILED：Target database is not up to date）

状态：OPEN / FIXED / BLOCKED
证据列说明：PG=真实 PostgreSQL 执行；E2E=真实四角色浏览器；SKIP=存在跳过

## P0 组：状态机、事务和租户完整性

### P0-01 OAuth callback/revoke/pause 竞态
- 不变量：revoke 完成后 callback/sync 永远不能把 source 恢复为 CONNECTED/OK；网络请求不得在长 DB 锁事务内；callback/pause/revoke 须为版本化或条件式状态迁移。
- RED 复现：见 tests/connectors/test_oauth_races.py（callback 网络挂起期间 revoke → 释放后断言最终状态仍是 revoked）。
- 根因：
  1. complete_oauth 在事务内执行 exchange_code 网络请求（service.py），且 `row.oauth_state="connected"` 为无条件赋值，可与 revoke 竞态覆盖 revoked。
  2. revoke_data_source 在事务内执行 provider 网络 revoke。
  3. runner.mark_sync_ok 无条件写 OK，revoke 后同步仍可写回 ok。
- 修改文件：app/scenarios/data_source/service.py；app/connectors/runner.py；app/connectors/sync.py；tests/connectors/test_oauth_races.py
- GREEN 证据：tests/connectors/test_oauth_races.py 2 passed（真实 PostgreSQL 并发，HRBP_RUN_CONCURRENCY_TESTS=true）
- PG 执行：是（隔离库 hrbp_seal_test，迁移 head 020）；真实外部：无（无真实企业）
- SKIP：无
- 状态：FIXED

### P0-02 HRCase 外部副作用 crash consistency
- 不变量：approval/execution claim 先可靠提交；外部操作带稳定 idempotency key；completion/failure 第二事务；重试不重复外部副作用。
- RED 复现：见 tests/hr_case/test_approval_crash_consistency.py（外部调用成功、completion commit 前崩溃 → 重试不得重复副作用）。
- 根因：execute_approved_write 中 claim 与外部副作用同事务（begin_tool_execution 的 CONSUMED 未先提交），崩溃回滚后 approval 恢复 APPROVED，重试会再次执行外部副作用。
- 修改文件：app/scenarios/hr_case_agent/agent_loop.py；app/scenarios/hr_case_agent/service.py；tests/hr_case/test_approval_crash_consistency.py
- GREEN 证据：test_approval_crash_consistency.py 1 passed（真 PG）；test_approval_decision_races.py 3 passed（真 PG）；test_approval_concurrency.py 1 passed；test_agent_loop/test_case_service 30 passed 无回归
- PG 执行：是（隔离库）；真实外部：无（无生产写工具执行器，501 TOOL_EXECUTOR_MISSING 仍显式暴露）
- SKIP：无
- 状态：FIXED

### P0-03 approval decision 原子性
- 不变量：approve/reject/expire 只有一个迁移获胜；UPDATE ... WHERE status=PENDING ... RETURNING；expiration 明确持久化不因异常回滚。
- RED 复现：tests/hr_case/test_approval_decision_races.py（并发 approve-vs-reject、approve-vs-expire、重复请求）。
- 根因：decide_approval 是 SELECT→check→set，无原子条件迁移；expired 状态赋值后抛异常会被回滚。
- 修改文件：app/scenarios/hr_case_agent/service.py；tests/hr_case/test_approval_decision_races.py
- GREEN 证据：test_approval_decision_races.py 3 passed（真 PG：approve-vs-reject 单胜、expire 持久化、重复决策 409）
- PG 执行：是；真实外部：无
- SKIP：无
- 状态：FIXED

### P0-04 knowledge decision 原子性
- 不变量：confirm/reject/assign 只从 open 迁移；并发一个获胜其余 409。
- RED 复现：tests/shared/test_knowledge_feedback_decision_races.py
- 根因：decide_candidate SELECT→check→set→commit，并发双写。
- 修改文件：app/scenarios/knowledge_feedback/service.py；tests/shared/test_knowledge_feedback_decision_races.py
- GREEN 证据：test_knowledge_feedback_decision_races.py 2 passed（真 PG：confirm/reject 单胜+409、已决候选不可重决）；既有 knowledge_feedback 测试 8 passed 无回归
- PG 执行：是；真实外部：无
- SKIP：无
- 状态：FIXED

### P0-05 tenant composite integrity
- 不变量：child→parent 关系在 DB 层拒绝跨租户绑定（不依赖服务层/RLS）。
- RED 复现：tests/shared/test_tenant_composite_fk.py（raw SQL 跨租户绑定必须失败）。
- 根因：多数模型只有单列 FK（source_id/org_unit_id/case_id 等），未加 (tenant_id,parent_id) composite FK；015/016/017 仅 work_tasks 已加。
- 修改文件：app/data/models/*（hr_case/connector/access_scope/user/chat/scenarios/infra/knowledge_base/data_source）；新 migration 020_tenant_composite_fk.py；tests/shared/test_tenant_composite_fk.py
- GREEN 证据：020 upgrade/downgrade/upgrade 往返成功（隔离库）；alembic check 无漂移；catalog 29 个 composite FK + 10 个父表 unique + RLS 全 FORCED + 015 partial index 幸存；test_tenant_composite_fk.py 2 passed（raw SQL 跨租户绑定被 DB 拒绝）
- PG 执行：是（隔离库 hrbp_seal_test）；真实外部：无
- SKIP：无
- 状态：FIXED（业务库 upgrade 未授权，未执行）

## 连接器组

### CONN-01 ingestion contract（received/processing/processed/failed/replayed）
- 不变量：只有业务副作用成功后才标记 processed。
- 已完成的最小安全基础：023 新增 `status`（received/processing/processed/failed）、处理/失败时间和错误字段；首次投递只进入 processing，重复投递只增加 replay_count，不能伪造 processed。`mark_event_processed` / `mark_event_failed` 仅供下游持久化成功/失败后显式调用。
- GREEN：test_sync_engine/test_webhooks 15 passed（真 PG）；首次投递 processed_at 为 NULL、replay 不得变成成功。
- 未完成/阻断：产品尚未定义企微/飞书消息应落入的业务对象（知识库、工单或其他），当前 runner 也没有该下游事务。因此不得调用 mark_event_processed 伪造已处理；需要确认目标对象、幂等键及失败重试策略后才可闭合。
- 状态：OPEN

### CONN-02 authorized_scope 结构化并后端强制
- 不变量：后端 runner 强制范围白名单，拒绝范围外。
- 修复：023 新增 `data_sources.authorized_scope_json JSONB`；管理 API 持久化/回显结构化范围；企微消息同步要求非空 `chat_ids`，并在消费前过滤范围外消息。旧自由文本记录没有 JSON 范围，保持 fail-closed，不能启动消息同步。
- GREEN：test_structured_scope_is_persisted_for_server_side_sync_enforcement + test_sync_runner 6 passed；023 upgrade→022→023 往返成功，目录确认 JSONB/RLS forced。
- 状态：FIXED

### CONN-03 同步 lease
- 不变量：同一 source 不允许两个同步并发；cursor 单调；pause/revoke 后任务停止或不能写回 OK；status 更新带条件。
- 修复：runner 用 PG session-scoped advisory lock（按 source 键控，连接断开自动释放）；mark_sync_ok/failed 条件化。
- GREEN：test_sync_lease.py 1 passed（真 PG 并发 409）；test_sync_runner.py 4 passed。
- 状态：FIXED

### CONN-04 同步不得在 HTTP 请求内跑多页/15s 级任务
- 不变量：真实后台 job 或有界小操作；删除"已有 Celery"误导注释。
- 当前 trigger_sync 在请求内跑 runner。状态：OPEN

### CONN-05 限流按 tenant/provider/source 多 worker 有效
- 当前 TokenBucket 进程内全局。状态：OPEN

### CONN-06 OAuth 完整语义
- authorize_url 参数全部 URL 编码（urlencode）；start_oauth 校验 https + 匹配登记回调；redirect_uri 持久化在迁移 021。
- 状态：FIXED（部分）/ 021 迁移待验证

### CONN-07 webhook 真实路由
- 修复：新增 app/access/routes/connector_webhooks.py + main.py 注册 + auth/rbac/rate_limit 中间件放行（provider 签名即认证）。
- GREEN：test_webhook_routes.py 2 passed（未认证可达、签名 403、replay 丢弃、challenge 握手）。
- 状态：FIXED

### CONN-08 无真实企业 → 降级 Level 1 / BLOCKED
- 无真实飞书/企微测试企业。文档/矩阵/UI 不得宣称 Level 2 生产就绪。
- 状态：OPEN

## 领域一致性

- KNOW-01：question_key 已改 100 字符 + SHA256 后缀防碰撞；test_knowledge_question_key.py 3 passed。FIXED
- TASK-01：active child=0 父进度定义；层级约束；completed parent 禁新增 child；children 全完成规则。OPEN
- TASK-02：advance 已改服务端原子 UPDATE + /tasks/{id}/advance 端点 + 前端改调；test_concurrent_advance_increments_exactly_once_per_click 1 passed。FIXED
- TASK-03：create idempotency key + 唯一约束。OPEN
- CULT-01：legacy claim 已改 UPDATE WHERE owner IS NULL RETURNING（条件行锁）；test_legacy_work_claim.py 6 passed。FIXED
- HRCASE-01：event seq 已加 per-case advisory xact lock；test_event_seq_concurrency.py 1 passed（真 PG 20 并发无重复）。FIXED
- HRCASE-02：run trace approvals 已按 requested_by=run 过滤（or_ None/run）。待测试。OPEN
- WEEKLY-01：period 已加 pattern 校验、action 已 enum(pattern)、source_ids 上限；history limit 已加 ge/le(API-01 部分)。FIXED(部分)
- API-01：history limit 已加 ge/le；legacy inventory 分页未做；N+1 审查未做。OPEN(部分)
- FE-01：Tasks 时间已改本地时区显示/编辑；测试按本地时间断言。FIXED
- FE-02：编辑弹窗用 query 数据初始化；刷新后不提交旧值；409 可操作反馈。OPEN
- FE-03：assignable owners 失败展示错误+重试。OPEN
- FE-04：client-generated idempotency key。OPEN
- FE-05：setup.ts act/strict warning 过滤已删除，33/33 vitest 通过（无 warning 掩盖）。FIXED
- FE-06：DataSourcesPage 已诚实标注（Level 4 才 operational、无授权不读数据），pause/resume/revoke 均真实接后端。FIXED

## 前端

- FE-01：Tasks 时间 UTC↔local 明确转换。FIXED
- FE-02：编辑弹窗用 query 数据初始化；刷新后不提交旧值；409 可操作反馈。OPEN
- FE-03：assignable owners 失败展示错误+重试。OPEN
- FE-04：client-generated idempotency key。OPEN
- FE-05：删除 setup.ts act/strict warning 过滤，修复真实等待。FIXED
- FE-06：Data Sources 页面诚实标记原型或接真实状态。FIXED（已有 Level 4 门控 + 诚实标注 + 真实 pause/resume/revoke）
- TASK-02 前端：advance 已改调服务端原子端点。FIXED

## 迁移与版本策略

- 现场：current=018 / heads=019，alembic check FAILED。
- 在隔离可丢弃 PG 验证 015/016/017/018/019/020/021/022/023 upgrade/downgrade/upgrade。
- 核验 pg_catalog：表、partial index、composite FK、RLS enabled/forced、policy predicate。
- retention/cleanup 策略：nonce/event/outbox。
- 不执行业务库 downgrade；业务库 upgrade 需另行授权。

## 门禁基线（最终执行）
- python -m pytest -q（含 HRBP_RUN_DB_SECURITY_TESTS/HRBP_RUN_CONCURRENCY_TESTS 激活）
- python -m ruff check . / python -m mypy app
- python -m alembic current / heads / check
- cd web && pnpm test / lint / build
- Playwright --list + 真实执行（四角色）
