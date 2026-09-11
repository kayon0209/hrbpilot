# HRBPilot 发布阻塞项修复实施方案（安全与对象级 ACL）

> 版本：1.0（待批准）
> 日期：2026-08-30
> 性质：**仅实施方案。本轮不修改生产代码、不执行迁移、不修改 `.env`、不提交、不推送。**
> 输入：`_audit_outputs/hrbpilot-production-ui-ux-final-spec-2026-08-30.md`（§3 权限、§10.4 安全、§7.10 审计）、`_audit_outputs/hrbpilot-ui-ux-upgrade-independent-audit-2026-08-30.md`（P0-1 / P1-1 / P1-2 / P1-3 / P1-4 / P2-1）
> 作者：独立修复规划（基于对当前工作树代码的逐文件复核，行号均为复核时实测）

---

## 0. 修复目标与总原则

1. **P0-1**：`/api/work-summaries` 增加能力门 + 聚合前对象级过滤；employee/admin 返回 403。
2. **P1-1**：员工请求分诊按负责人与显式组织授权过滤；HRBP 不得读未指派请求正文，经理不超出授权组织范围。
3. **P2-1 → 发布阻塞**：删除 `ROLE_HIERARCHY` 数值等级与"某角色及以上"授权模式（`require_role("hrbp")` 允许 hr_manager/admin 顺位继承——这正是《方案》§二点名禁止的继承），统一 capability + object ACL；capability 路由映射降级为"冗余防线"，**不得是唯一防线**。
4. **P1-2**：数据接入凭据禁用 XOR 存储，双方案（正式 KMS / KMS 前安全降级），明确推荐项。
5. **P1-3**：暂停/恢复/撤销/分诊/知识反馈确认驳回/周报发布/权限变更写持久化审计 + 管理员审计入口。
6. **P1-4**：旧后端实例（8001）验收证据作废；修复后从当前工作树重启全栈再四角色冒烟。
7. 全程 fail-closed：KMS 未配置、审计写入失败、历史数据无 owner 时宁可拒绝服务也不放宽边界。

**总原则**：
- 修复后的防线层次：JWT 认证 → RBAC 中间件粗粒度能力门 → 路由内能力断言（冗余）→ **service 层对象级 ACL（真正边界）→ DB RLS（兜底）**。
- 同租户内跨用户访问一律 404（对象存在性不泄露）；能力不足一律 403；未知角色一律 403（现状已 fail-closed，保持）。
- 历史数据回填**不猜测**：无 owner 的行进入"无人认领"态（`acl_owner_unassigned`），不默认归给管理员、不默认归给全租户可见。

---

## 1. 数据库 schema 变更（需用户单独批准）

新迁移 `012_object_acl_and_audit.py`（down_revision = `011_data_sources`）。**逐项 DDL 如下；执行前需在迁移前快照验证（§1.5）。**

### 1.1 新表 `org_units`（组织范围）

```sql
CREATE TABLE org_units (
  id            VARCHAR(36) PRIMARY KEY,
  tenant_id     VARCHAR(36) NOT NULL,          -- RLS
  name          VARCHAR(200) NOT NULL,        -- 业务语言：部门/团队名
  parent_id     VARCHAR(36) REFERENCES org_units(id),  -- 可为 NULL（根）
  created_at / updated_at ...                 -- TimestampMixin
);
ALTER TABLE org_units ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_units_tenant_isolation ON org_units
  USING (tenant_id = current_setting('app.tenant_id', true));
CREATE INDEX ix_org_units_tenant ON org_units(tenant_id);
CREATE INDEX ix_org_units_parent ON org_units(parent_id);
```

**设计决策：组织树从最小树开始**（每租户一个默认根 org unit），不支持"同租户=全范围"推断——《方案》§二十一明确"经理组织范围：默认显式授权，不从同租户或职级自动推断"。HRCase.subject_ref 不引入组织列（Phase 4 设计如此），组织范围授权表承担经理可见性。

### 1.2 新表 `user_scope_grants`（显式授权，经理组织范围）

```sql
CREATE TABLE user_scope_grants (
  id            VARCHAR(36) PRIMARY KEY,
  tenant_id     VARCHAR(36) NOT NULL,              -- RLS
  user_id       VARCHAR(36) NOT NULL REFERENCES users(id),
  org_unit_id   VARCHAR(36) NOT NULL REFERENCES org_units(id),
  grant_type    VARCHAR(20) NOT NULL,              -- read | triage | manage
  granted_by    VARCHAR(36) NOT NULL REFERENCES users(id),
  reason        VARCHAR(500),                      -- 授权理由（审计用）
  expires_at    TIMESTAMP WITH TIME ZONE,          -- 可为 NULL（长期）；临时授权支持过期
  revoked_at    TIMESTAMP WITH TIME ZONE,          -- 撤销时间
  created_at / updated_at ...
);
ALTER TABLE user_scope_grants ENABLE ROW LEVEL SECURITY;
CREATE POLICY user_scope_grants_tenant_isolation ON user_scope_grants
  USING (tenant_id = current_setting('app.tenant_id', true));
CREATE INDEX ix_usg_tenant_user ON user_scope_grants(tenant_id, user_id);
CREATE INDEX ix_usg_tenant_org ON user_scope_grants(tenant_id, org_unit_id);
-- 有效授权 = revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now())
```

**数据流**：hr_manager 的可见组织范围 = 自己 `user_scope_grants` 中有效记录的 org unit 集合及其**子孙**（`WITH RECURSIVE` 下钻，缓存到进程内 TTL 60s）。没有授权记录的经理 = 空范围（fail-closed），不是全租户。

### 1.3 既有业务表补所有权列（AsyncTask / WeeklyReport / EmployeeRequest / KnowledgeFeedbackCandidate / CultureContent / InterviewDigest / InsightReport）

统一加三列（`owner_user_id` = 创建者，`assigned_user_id` = 当前被指派处理人，`owner_unassigned BOOLEAN` = 回填标记）：

```sql
-- async_tasks（面谈/声音任务的载体，work-summaries 的数据源①）
ALTER TABLE async_tasks ADD COLUMN owner_user_id VARCHAR(36) REFERENCES users(id);
ALTER TABLE async_tasks ADD COLUMN assigned_user_id VARCHAR(36) REFERENCES users(id);
ALTER TABLE async_tasks ADD COLUMN owner_unassigned BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX ix_async_owner ON async_tasks(tenant_id, owner_user_id);
CREATE INDEX ix_async_assigned ON async_tasks(tenant_id, assigned_user_id);

-- weekly_reports（数据源②）
ALTER TABLE weekly_reports ADD COLUMN owner_user_id VARCHAR(36) REFERENCES users(id);
ALTER TABLE weekly_reports ADD COLUMN assigned_user_id VARCHAR(36) REFERENCES users(id);
ALTER TABLE weekly_reports ADD COLUMN owner_unassigned BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX ix_weekly_owner ON weekly_reports(tenant_id, owner_user_id);

-- employee_requests：已有 created_by（员工侧对象键，保留）+ hr_owner_id（已存在但目前是死列，激活）；
--   增加员工组织归属，供经理组织过滤
ALTER TABLE employee_requests ADD COLUMN org_unit_id VARCHAR(36) REFERENCES org_units(id);
ALTER TABLE employee_requests ADD COLUMN owner_unassigned BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX ix_emp_req_org ON employee_requests(tenant_id, org_unit_id);
CREATE INDEX ix_emp_req_owner ON employee_requests(tenant_id, hr_owner_id);

-- knowledge_feedback_candidates（经理行动中心对象）
ALTER TABLE knowledge_feedback_candidates ADD COLUMN owner_user_id VARCHAR(36) REFERENCES users(id);
ALTER TABLE knowledge_feedback_candidates ADD COLUMN owner_unassigned BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX ix_kfc_owner ON knowledge_feedback_candidates(tenant_id, owner_user_id);

-- culture_contents / interview_digests / insight_reports：同 async_tasks 三列模式（本轮最小化：只加 owner_user_id + owner_unassigned）
```

**为什么 weekly/async 都要列**：今日工作聚合的三个数据源（`work_summary/service.py:243-245`：AsyncTask、WeeklyReport、ChatSession）中前两个没有用户归属列（复核：`infra.py:17-28`、`scenarios.py:42-51` 均无），这正是 P0-1 的结构性根因。ChatSession 已有 `user_id`（`chat.py:15`），不动。

**外键注意**：`REFERENCES users(id)` 在跨表并发写入时没有冲突风险（都是新增可空列）；但 **RLS 兼容性**——users 表自身的 RLS 策略对子表 FK 校验的影响需在迁移中实测（PG 外键检查以表所有者身份运行，通常不受目标表 RLS 影响；若测试环境出现策略冲突，回退方案：FK 降为逻辑外键（无 REFERENCES 约束）+ service 层校验，此为**待迁移验证点 A**）。

### 1.4 审计事件表 `admin_action_logs`（区别于既有 `audit_logs`）

既有 `audit_logs`（`infra.py:34-51`）是**问答质量流水**（scenario 维度、confidence、tokens）——复用它记录权限/撤权事件会污染语义且缺字段。新建专用表：

```sql
CREATE TABLE admin_action_logs (
  id            VARCHAR(36) PRIMARY KEY,
  tenant_id     VARCHAR(36) NOT NULL,              -- RLS
  actor_user_id VARCHAR(36) NOT NULL,
  actor_role    VARCHAR(20) NOT NULL,
  action        VARCHAR(50) NOT NULL,  -- data_source.pause | data_source.resume | data_source.revoke |
                                       --   data_source.create | employee_request.triage |
                                       --   knowledge_feedback.confirm | knowledge_feedback.assign |
                                       --   knowledge_feedback.reject | weekly_report.publish |
                                       --   weekly_report.save | scope_grant.grant | scope_grant.revoke |
                                       --   user.role_change
  object_type   VARCHAR(30) NOT NULL,  -- data_source | employee_request | knowledge_candidate | weekly_report | user | scope_grant
  object_id     VARCHAR(64) NOT NULL,
  object_label  VARCHAR(200),           -- 人话对象名（"飞书 · 回归测试接入"），供管理员审计页展示
  detail_json   TEXT,                   -- {"reason": "...", "from": "...", "to": "..."} 敏感值脱敏后入库
  outcome       VARCHAR(10) NOT NULL,  -- success | denied | error
  request_id    VARCHAR(64),
  created_at    TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()   -- append-only，无 updated_at
);
ALTER TABLE admin_action_logs ENABLE ROW LEVEL SECURITY;
CREATE POLICY admin_action_logs_tenant_isolation ON admin_action_logs
  USING (tenant_id = current_setting('app.tenant_id', true));
CREATE INDEX ix_aal_tenant_time ON admin_action_logs(tenant_id, created_at DESC);
CREATE INDEX ix_aal_action ON admin_action_logs(tenant_id, action);
```

**admin 审计入口**（P1-3 的查询侧）：

```sql
-- 管理员审计只读端点（新路由组 /api/audit，capability=audit_read 已在 rbac.py:88 预留）
GET /api/audit/admin-actions?limit=&before=&action=&object_type=
-- 返回按时间倒序的 admin_action_logs（RLS 限定本租户）；
-- 无 DB 时 fail-closed 返回 503"审计记录暂不可用"，不返回空列表冒充"无记录"
```

### 1.5 迁移、回滚与兼容策略

**迁移前快照（强制）**：
```
docker exec hrbpilot-postgres-1 pg_dump -U postgres -d hrbp_workbench \
  --table=async_tasks --table=weekly_reports --table=employee_requests \
  --table=knowledge_feedback_candidates --table=data_sources --table=users \
  > _audit_outputs/pre-012-backup-$(date +%Y%m%dT%H%M).sql
```
（审查环境已确认 docker postgres 容器名 hrbpilot-postgres-1；生产命令按部署形态替换。）

**执行顺序**（012 单文件五步，每步幂等）：
1. 建 `org_units` + 每租户默认根（`INSERT ... SELECT DISTINCT tenant_id FROM users WHERE NOT EXISTS`，org unit 名"全组织（默认）"——注意：默认根**仅作为组织树锚点，不自动授予任何经理范围**）；
2. 建 `user_scope_grants`；
3. 业务表加列（全部可空/默认 FALSE，**不锁表**——ALTER ADD COLUMN nullable 在 PG 是元数据操作）；
4. 回填 owner（§2）；
5. 建 `admin_action_logs` + 索引 + RLS。
   索引在回填**之后**建，避免回填期间索引维护开销。

**回滚**（`downgrade`）：按逆序 drop 新表/新列。回填列可安全 DROP（新代码只读它们；旧代码不认识它们）。**不可自动回滚的**：回填产生的 `owner_user_id` 值与 admin_action_logs 记录——downgrade 前需再快照（语义上审计表 append-only，downgrade 应拒绝在已有审计行时执行：`RAISE EXCEPTION 'audit rows exist; archive first'`，fail-closed）。

**兼容窗口**：迁移先于代码部署执行（新列可空、旧代码忽略）；代码部署后旧列（如 `AsyncTask.progress`）不动，无破坏性变更。**不做 online double-write**——单机部署形态下迁移+重启窗口即可。

**待验证点 A（FK × RLS）**、**B（asyncpg + set_config 事务语义在批量回填下）**、**C（`WITH RECURSIVE` 在 10 万 org 节点的查询计划）** 在迁移演练环境先跑（§6.3）。

---

## 2. 历史数据回填（无 owner 不得默认归给全租户/管理员）

**规则**：
1. **能确定的**：
   - `chat_sessions` 已有 user_id → 不需要回填。
   - `async_tasks`/`weekly_reports` 等：当前生产数据**全部来自单用户演示种子**（审查确认 tenant-001 的 4 个用户，且面试/声音任务由 hrbp 会话创建）。回填脚本用**确定性映射**：`async_tasks.created_at` JOIN `chat_sessions`（同租户、scenario 时间窗）→ 推断创建者；无法对应的行**不猜**。
2. **不能确定的**：`owner_unassigned = TRUE`，`owner_user_id = NULL`。**可见性规则 = fail-closed**：
   - hrbp：看不到任何 `owner_unassigned=TRUE` 的行（宁可少看，不多看）；
   - hr_manager：`owner_unassigned=TRUE` 的行**只出现在"团队待处理-未认领"聚合中且不含正文**（仅标题+状态+创建时间），因为经理页的职责就是发现无人处理的事项——这是唯一的例外，且**仅元数据**；
   - admin：完全不可见（403 在端点层就挡住）；
   - 一旦任何用户"认领"（triage/assign 动作），写 `assigned_user_id`，`owner_unassigned` 翻 FALSE，此后按正常 ACL。
3. **演示种子**（17 条"回归测试接入"等）：标记 `owner_unassigned=TRUE` 且不回填，同时在 admin 数据接入页加"仅显示测试数据"筛选（修复 P3-4 的顺手项，不改业务代码逻辑只加查询参数）。

**回填脚本形态**：`app/data/migrations/backfill_012_owner.py`（只读推断 + 明细打印，`--dry-run` 默认；`--apply` 才写）。输出报告落 `_audit_outputs/backfill-012-report.md`，逐行"行 id → 归属/未认领 + 依据"。

**禁止**：任何形式的 `owner_user_id = admin_id`、`owner_user_id = tenant 全体`、或"同租户即默认可读"。

---

## 3. capability / 对象 ACL / 组织范围数据流

### 3.1 统一访问判定核心（新模块 `app/access/acl.py`）

```
请求 → AuthMiddleware（JWT→ user_id/role/tenant_id）
     → RBACMiddleware（ROUTE_CAPABILITY_MAP：能力存在性检查，403）
     → 路由装饰器 @require_capability("x")（冗余断言，防映射表遗漏——修复 P2-1"唯一防线"问题）
     → service 层 acl.py：
         can_read_object(user, obj)   # owner_user_id == user 或 assigned_user_id == user
         hr_scope(user)               # hrbp: {self}; hr_manager: grants→org subtree; 无授权=∅
         employee: created_by == user
     → DB RLS 兜底（tenant_id 级；对象级在 service 层，RLS 不承担对象判定——保留现状）
```

**关键点：RBAC 中间件不再是唯一防线**。每个业务路由显式 `@require_capability(...)`（新增装饰器，语义= capability in request.state.role_capabilities），capability 路由映射表遗漏时路由自身仍 403。映射表、装饰器、service ACL 三层各自独立可测。

### 3.2 删除 ROLE_HIERARCHY（发布阻塞，非技术债）

**现状**（复核）：`decorators.py:17-22` 定义 `employee:0→admin:3`；`require_role`（`decorators.py:66-69`）用 `user_level < min_level` 判断——`@require_role("hrbp")` 实际允许 hrbp+hr_manager+admin 三角色顺位通过（admin 通过中间件前置拦截是**巧合**而非设计：若未来某路由忘记进 ROUTE_CAPABILITY_MAP，require_role 会把 admin 放进来——正是 work-summaries 的翻版风险）。25 处引用（interview 5、voice 4、weekly 4、culture 4、kb 5、settings 3）。

**改法**：
1. 删除 `ROLE_HIERARCHY` 与 `require_role`（`decorators.py` 整段）；
2. 25 处替换为显式能力断言：
   - `@require_role("hrbp")` → `@require_capability("interview_digest" | "voice_insight" | "weekly_report" | "culture_content")`（按路由所属域）
   - `@require_role("hr_manager")`（kb.py 5 处）→ `@require_capability("kb_management")`
   - `@require_role("admin")`（settings.py 3 处）→ `@require_capability("settings")`
3. 全库 `grep -rn "ROLE_HIERARCHY\|require_role\|min_level\|user_level"` 归零为验收条件；
4. 防回归测试：`test_no_numeric_role_hierarchy` 断言模块中不存在上述符号（用 `inspect`/import 检查），并测试 `@require_capability("interview_digest")` 拒绝 hr_manager 之外的所有角色、拒绝 admin（HRBP 域不向经理外的更高角色放行——**注意**：hr_manager 需要访问 interview/voice/weekly/culture，所以这些域的能力断言对 hrbp+hr_manager 都放行；这是 capability 集合本来语义，与数值等级不同点在于：admin 集合里没有这些 capability，天然放不进来，且**能力不可被"更高角色"自动获得**）。

### 3.3 `/api/work-summaries`（P0-1）

- `rbac.py ROUTE_CAPABILITY_MAP` 增 `"/api/work-summaries": "work_summary_view"`；
- `ROLE_CAPABILITIES`：hrbp、hr_manager 加 `work_summary_view`；employee、admin **不加** → 中间件层 403；
- 路由内再 `@require_capability("work_summary_view")`（冗余）；
- `collect_work_summaries(tenant_id, user_id, role)` 重写过滤：
  - `_collect_async_tasks`：`owner_user_id == user OR assigned_user_id == user`（hrbp）；hr_manager 附加 `org scope`（async_tasks 无 org 列——经理范围通过"任务的 owner 属于授权 org 的用户集合"实现：`owner_user_id IN (SELECT user_id FROM user_scope_grants WHERE org in subtree)`——**简化替代**：由于 org 树+用户归属当前未建（users 无 org_unit_id），第一阶段经理范围= 直接授权**用户清单**而非 org 子树（user_scope_grants 无 org 也能表达"授权给这些用户的数据"？**否**——语义反了。**第一阶段决策：hr_manager 与 hrbp 同规则（本人创建/被指派）+ 团队待处理页走 org 例外（见 §3.4）**。org 子树完整支持列入 backlog，理由：当前租户内实际只有 1 个 HRBP+1 个经理，org 表建立后数据自然生长，但 work-summary 的经理 org 过滤需要 users.org_unit_id 列——加入 012 迁移（ALTER users ADD org_unit_id），使完整链路一次成型，只是**回填策略**保守：users 的 org 归属由管理员手工配置，不自动推导）。
  - `owner_unassigned=TRUE` 行：hrbp 不可见；hr_manager 仅元数据（见 §2）；
  - `_collect_weekly_reports`：同 owner 过滤；
  - `_collect_policy_sessions`：已按 user_id（现状正确，保留）；
  - 修正 `service.py:12-13` 的注释使其与实现一致（P0-1 修复项 3）。
- **403 vs 404**：employee/admin 在中间件层 403（能力缺失是"这类角色整体无权"）；同租户其他 HRBP 的对象在工作流中**不单独出现**（聚合只返回本人的），无 404 场景；若未来提供单对象端点（如 `/api/work-summaries/{id}`），同租户越权 → 404。

### 3.4 `/api/hr-requests` 分诊（P1-1）

- `hr_list_open(tenant_id, actor_id, actor_role)`：
  - hrbp：`hr_owner_id == actor OR (hr_owner_id IS NULL AND assigned 方式)`——**未指派请求 hrbp 不可见正文**。但 hrbp 需要能"抢单"吗？《方案》§3.2 处理 Request= "被指派范围"。**决策：未指派请求只出现在 hr_manager 的"待分派"列表（元数据+首句），hrbp 只看到指派给自己的**。分派动作本身是经理职责（triage 页新按钮"指派给我/指派给 HRBP"）。
  - hr_manager：`org_unit_id IN (有效授权 org 子树)` 的请求 + 全部"未指派"请求（分派职责）；无授权的经理仍能看到未指派列表（否则无法履行分派），但**看不到已指派给他人的正文**（只见元数据行）。
  - 正文（description/hr_note）仅当：hr_owner_id==actor 或（经理且请求属于授权 org）。
- `hr_triage`：增加 actor 参数；写入 `hr_owner_id`（首次处理= 认领）+ `assigned`；越权（非 owner 非授权经理）→ **404**（对象存在性不泄露）。
- **403 vs 404 矩阵**：
  | 场景 | 码 |
  |---|---|
  | employee 访问 /api/hr-requests | 403（RBAC 已有） |
  | hrbp 读未指派请求正文 | 列表不含该行（不可见=无信息泄露）；若直接猜 id 调 triage | **404** |
  | hrbp B 对 hrbp A 的已指派请求 triage | **404** |
  | 经理对授权 org 外的已指派请求 triage | **404** |
  | admin 调 /api/hr-requests | 403（RBAC 已有） |
  | 跨租户任何对象 | 404（RLS 天然） |
- 现有 `hr_triage` 的 `hr_owner_id = row.hr_owner_id`（service.py:175，死代码）替换为认领语义。

### 3.5 周报/面试/声音/文化域的对象 ACL（同模式，随 P2-1 改造一并落地）

- `weekly_report.py` 的 `/sources /generate /save /history`：sources/generate 按 `AsyncTask.owner_user_id==user`（生成用的 source 不得读取他人任务）；save/history 按 `WeeklyReport.owner_user_id==user`；发布动作额外写审计（§4）。
- `interview_digest.py` 的 `/progress /result /history`：`get_task_status(task_id, tenant_id)`（当前仅 tenant 过滤）增加 owner 校验——非 owner 的同租户用户轮询他人 task_id → **404**（与现有 NotFoundError("Task") 一致，天然统一）。
- `voice_insight.py` 同上。
- `culture_content.py`、`kb.py`：本轮**不引入对象级**（文化内容/知识库按《方案》3.2 属"自己或被授权范围"，但当前单租户单用户实际下没有跨用户场景，改造成本大收益小——**列入待批 scope 决策**，默认本轮只加 capability 断言替换 require_role，不动对象层）。这是唯一一处刻意分层：权限模型统一到 capability+ACL 的"骨架"，对象层按数据敏感度排序渐进（Request/任务/周报=先行，文化/KB=后续）。

### 3.6 前端影响（最小）

- `TodayPage`/`TasksPage`：数据变少（过滤后）不需 UI 改动；`WorkSummary` 类型不变；
- `HrRequestsPage`：经理视图增加"指派"动作按钮（后端字段已在 triage body 中）；未指派区显示"待分派"分组；
- `DataSourcesPage`：撤销原因改 ConfirmDialog（P3-2 顺手项，可选）；
- 无新导航项（审计入口在 admin 导航加"审计记录"，P2-8 的一部分——**本方案只实现审计 API+页面骨架，完整审计 UI 归属 P2-8 不阻塞发布**）。

---

## 4. 审计写入（P1-3）

**新函数 `app/shared/admin_audit.py`**：

```python
async def record_admin_action(
    tenant_id, actor_user_id, actor_role, action, object_type, object_id,
    object_label, detail: dict | None, outcome: "success|denied|error", request_id: str | None,
    *, session: AsyncSession | None = None,   # 复用调用方事务
) -> str | None
```

**fail-closed 语义（关键设计）**：
- 高影响动作（`data_source.revoke`、`data_source.pause`、`scope_grant.grant/revoke`、`user.role_change`、`weekly_report.publish`）：**审计写失败 → 业务动作回滚**（同一事务内完成：`session` 参数传入，业务 commit 与审计 insert 绑定）。理由：这些动作的不可审计性不可接受（撤权无记录=合规空洞）。
- 普通动作（`employee_request.triage`、`knowledge_feedback.confirm/assign/reject`、`data_source.resume/create`、`weekly_report.save`）：**审计写失败 → 动作成功 + 告警日志 + 进程内计数器**（重试队列 1 次），不阻塞业务（分诊是员工服务时效链路）。告警在 `/api/ready` 的扩展检查暴露 `audit_degraded: true`（admin 系统状态可见——P2-8 的系统状态页部分兑现）。
- 无 DB（dev mock 模式）：动作拒绝（高影响类）或降级 logger（普通类）——与现有 `_check_db_available` 模式一致，但**高影响类 fail-closed 不降级**。

**接入点清单**（文件:行号 复核值）：
| 动作 | 位置 | 类别 |
|---|---|---|
| data_source pause/resume/revoke | `data_source/service.py:174-263` 三个函数 | pause/revoke 高影响；resume 普通 |
| data_source create（含凭据） | `service.py:120-147` | 高影响（凭据进入系统） |
| employee_request triage | `service.py:137-183` | 普通 |
| knowledge_feedback decide | `service.py:179-220` | 普通 |
| weekly_report publish/save | `routes/weekly_report.py:170-208`（save 在路由层，publish 也在） | publish 高影响；save 普通 |
| 权限变更（scope_grant、role change） | 新管理端点（本轮新增 grant/revoke API；role_change 挂钩 user 表更新——**本轮只建表+API，不建用户管理 UI**） | 高影响 |

**脱敏规则**：`detail_json` 白名单字段（reason/from/to/status），**永远不进**凭据、description 原文、hr_note 全文（只留 80 字符摘要 + hash 前 8 位）。`object_label` 用业务名。

**查询端点**：`GET /api/audit/admin-actions`（capability `audit_read`，仅 admin）；分页 `limit<=100&before=<cursor ts>`；`action`/`object_type` 过滤；无 DB→503。

---

## 5. 凭据存储（P1-2）双方案

### 方案 A（推荐·先落地）：KMS 前安全降级——**禁存真实凭据**

1. **删除** `_encrypt_credential` XOR 实现（`data_source/service.py:106-117`）与 `credential_ref="tenant-key:..."` 假引用（:142）；
2. `CreateDataSourceBody.credential` 字段保留但**服务端拒绝非空值**（ValidationError："当前版本尚不支持在系统内保存访问凭据。请通过受控的外部流程完成授权，凭据由平台管理员在部署层配置"）；
3. `credential_encrypted`/`credential_ref` 列保留为 NULL（schema 不动——列本来 nullable）；
4. 前端 `DataSourcesPage.tsx:66` 访问凭据输入框改为说明文案："凭据由部署层受控管理，本页不录入"（UI 改动一处）；
5. **效果**：XOR 路径物理消失，任何人无法通过 API 把真实凭据写进弱加密存储。渠道接入的"认证状态"仍由 certification_level 表达（真实授权动作发生在部署层/OAuth 流程，那是 Phase 5 后续）。
6. 与《方案》10.4 的关系：10.4 要求"租户级加密凭据或外部密钥管理"——**不录入即满足**（无凭据可泄漏是比弱加密更强的安全态）。四级认证推进机制（P2-10）不受影响。

### 方案 B（后续·KMS 正式方案——设计预留，不在本轮实施）

1. 信封加密：每租户 DEK 由 KMS 主密钥（CMK）包裹；`credential_encrypted` 存 `envelope_v1 || nonce || ciphertext || tag`；`credential_ref` 存 `kms://<key-id>`；
2. KMS 适配器接口（新 `app/shared/kms.py`）：`encrypt(plaintext, aad=tenant_id) / decrypt(ref, aad)`，实现按部署形态二选一：云 KMS（阿里云 KMS / 腾讯云 KMS，凭据走环境变量注入的 AccessKey，**不落库**）或 Vault Transit；
3. AAD 绑定 tenant_id → 密文跨租户不可解；
4. 轮换：CMK 轮换不重写数据（信封模式），DEK 轮换走"新建渠道凭据"路径；
5. **触发条件**：第一个真实客户需要在线授权（OAuth refresh token 存储）时启动方案 B；在此之前方案 A 是终态而非过渡（可长期停留）。

**推荐**：方案 A 立即实施（改动 ~15 行 + 1 处 UI 文案），方案 B 作为"当 OAuth 渠道落地时"的设计预留文档化于本方案。**两者不冲突**：A 删除的 XOR 代码就是 B 的占位清除。

### KMS 未配置时的 fail-closed（两种方案下的统一行为）

- 方案 A 下不存在"KMS 未配置"状态（无加密路径可走）；
- 若未来方案 B 部署但 KMS 不可达：`decrypt` 抛出 → 同步任务标记 failed（last_error="凭据服务不可用"），**不回退明文、不重试明文**；创建渠道时 `encrypt` 不可达 → 拒绝保存凭据（同方案 A 行为）。

---

## 6. 文件清单与修改顺序（依赖序）

### 6.1 代码修改（按提交粒度分组，每组独立可测、可回滚）

**Group 1 — ACL 基础设施 + capability 断言（无 schema 依赖，先行）**
1. 新 `app/access/capability.py`：`require_capability(...)` 装饰器 + `ROLE_CAPABILITIES` 迁入（从 rbac.py 移出，单一来源）；
2. 改 `app/access/middleware/rbac.py`：import 自 capability.py；`ROUTE_CAPABILITY_MAP` 增 `/api/work-summaries`；`ROLE_CAPABILITIES` hrbp/hr_manager 增 `work_summary_view`；中间件把解析出的 capability 集合挂 `request.state.role_capabilities`（供装饰器读）；
3. 删 `app/access/middleware/decorators.py` 的 `ROLE_HIERARCHY`+`require_role`（保留 `require_auth`）；25 处路由替换为 `@require_capability(...)`（§3.2 清单）；
4. 新 `app/access/acl.py`：`can_read_object` / `hr_scope` / `visible_user_ids_for_manager`；
5. 测试：`tests/shared/test_capability_decorators.py`（新）、改造 `test_capability_rbac.py`（增 work-summaries 403 断言）。

**Group 2 — 迁移 012（需批准后执行；代码先行写好但不 deploy）**
6. 新 `app/data/migrations/versions/012_object_acl_and_audit.py`（§1 全部 DDL + 回填函数骨架调用）；
7. 新 `app/data/migrations/backfill_012_owner.py`（§2，dry-run 默认）；
8. 改 `app/data/models/infra.py`（AsyncTask+3 列）、`scenarios.py`（WeeklyReport/EmployeeRequest/KFC/CultureContent/InterviewDigest/InsightReport 补列）、`user.py`（org_unit_id）、新 `app/data/models/org.py`（OrgUnit/UserScopeGrant/AdminActionLog）；
9. `app/data/models/__init__.py` 注册新模型。

**Group 3 — work-summaries 对象过滤（依赖 Group1+2）**
10. 改 `app/scenarios/work_summary/service.py`：collect_work_summaries 签名加 role/actor；三个 _collect 加 owner/org 过滤；修注释；
11. 改 `app/access/routes/work_summary.py`：传 actor 上下文 + `@require_capability("work_summary_view")`；
12. 测试：`tests/shared/test_work_summary_acl.py`（新，§7 RED 清单）。

**Group 4 — 分诊对象 ACL**
13. 改 `app/scenarios/employee_request/service.py`：hr_list_open/hr_triage 签名与过滤逻辑（§3.4）；hr_owner_id 认领语义；未指派→经理待分派；
14. 改 `app/access/routes/employee_request.py`：传 actor；triage 越权 404；
15. 测试：改造 `tests/shared/test_employee_request_acl.py`（增两 HRBP/两经理/越权 triage 用例）。

**Group 5 — 面试/声音/周报任务级 ACL**
16. 改 `interview_digest/orchestrator.py:240`、`voice_insight/orchestrator.py:215`：get_task_status 增 owner 参数（非 owner 同租户 → None→404）；
17. 改两 orchestrator 的 start_async_task/persist：写 owner_user_id；
18. 改 `weekly_report.py` 路由四处 + `weekly_report/orchestrator.py:_store_report:136`（写 owner）；sources/generate 过滤；
19. 改 `app/scenarios/tasks.py`（Celery 侧 `_update_task`/`_persist_*` 不需要 owner——任务创建时已写）；
20. 测试：`tests/shared/test_task_object_acl.py`（新）。

**Group 6 — 审计**
21. 新 `app/shared/admin_audit.py`（§4）；
22. 接入 7 处（§4 表）；高影响与业务同事务；
23. 新 `app/access/routes/audit.py`：GET /api/audit/admin-actions；`main.py` 挂路由；`rbac.py` ROUTE_CAPABILITY_MAP 已有 audit_read 映射（88 行）无需改；
24. 测试：`tests/shared/test_admin_audit.py`（新）。

**Group 7 — 凭据方案 A**
25. 改 `data_source/service.py`：删 XOR（106-117）、删假 ref（142）、create 拒绝 credential 非空；
26. 改 `web/src/features/data-sources/DataSourcesPage.tsx:66`：凭据输入框 → 说明文案；
27. 测试：`tests/shared/test_data_source_contract.py` 增"凭据被拒"断言。

**Group 8 — 前端小改**
28. `HrRequestsPage.tsx`：经理"指派"动作 + 待分派分组；
29. `navigation.ts`：admin 导航增"审计记录"入口（指向新审计页骨架 `features/audit/AuditLogPage.tsx`——最小列表+过滤）；
30. `web/tests/` 对应测试更新。

### 6.2 明确不改的文件（防 scope 蔓延）

- `policy_qa.py`、`PolicyQaPage.tsx`（宣言 P2-3 是另一轮文案工作，不混入安全修复）；
- `AppShell`/`tokens.css`（P2-11/12 同上）；
- `chat.py` ChatSession（user_id 已正确）；
- RAG 管道、guardrails、评测链路。

### 6.3 演练环境（迁移验证 A/B/C）

在 docker compose 的 postgres 上建 `hrbp_workbench_rehearsal` 库 → 跑 012 → 验证：FK×RLS 交互、回填 dry-run 输出、`WITH RECURSIVE` 计划、downgrade 路径。通过后才允许对开发库执行。

---

## 7. RED→GREEN 测试清单（先写测试见红，再实现见绿）

新测试文件 4 个 + 改造 2 个。测试环境形态沿用现有 `test_capability_rbac.py` 模式（TestClient + 注入 JWT，无 DB 依赖的中间件测试）+ 少量需要 DB 的 ACL 测试（标记 `@pytest.mark.db`，本地缺 aiosqlite 的场景在 CI 容器跑——现状 25 个 error 的环境问题不在本轮修复范围，DB 测试走真实 postgres 演练库）。

**矩阵用户**：employee(u1)、hrbp(u2)、hrbp(u3，第二个 HRBP)、mgr-A(u4，授权 org-1)、mgr-B(u5，授权 org-2)、admin(u6)、跨租户用户(u7@tenant-2)。种子：u2 的任务/周报/请求若干，u3 的同类数据，org-1 下员工请求 R1，org-2 下 R2，未指派请求 R3。

| # | 场景 | 期望 | 测试位置 |
|---|---|---|---|
| T1 | employee GET /api/work-summaries | 403 | test_capability_rbac（改） |
| T2 | admin GET /api/work-summaries | 403 | 同上 |
| T3 | hrbp-u2 GET /api/work-summaries | 200，仅 u2 owner/assigned 的条目；无 u3 条目 | test_work_summary_acl（新，DB） |
| T4 | hrbp-u3 同上 | 200，仅 u3 条目 | 同上 |
| T5 | mgr-A（授权 org-1）| 200：自己条目 + org-1 范围内元数据 | 同上 |
| T6 | mgr-B（授权 org-2）| 200：不出现 org-1 条目 | 同上 |
| T7 | owner_unassigned 行 | hrbp 不可见；mgr 元数据可见 | 同上 |
| T8 | u2 轮询 u3 的 task_id /api/interview-digest/progress | 404 | test_task_object_acl（新，DB） |
| T9 | u3 读 u2 周报 id /api/weekly-report/save（越权发布） | 404 | 同上 |
| T10 | employee u1 GET /api/hr-requests | 403 | 既有保留 |
| T11 | hrbp-u2 GET /api/hr-requests | 200：仅 hr_owner=u2 的请求；R3（未指派）不出现在 u2 列表 | test_employee_request_acl（改，DB） |
| T12 | mgr-A GET /api/hr-requests | 200：R1 正文 + R3 元数据（待分派）；R2（org-2）不出现 | 同上 |
| T13 | mgr-B GET | R2 可见；R1 不可见 | 同上 |
| T14 | hrbp-u2 POST triage on R1（非 owner 非授权） | 404 | 同上 |
| T15 | hrbp-u3 POST triage on u2 已指派请求 | 404 | 同上 |
| T16 | mgr-A triage R2（org 外已指派） | 404 | 同上 |
| T17 | mgr-A triage R3（未指派→认领） | 200 + hr_owner_id 落 mgr-A + 审计行 | 同上 + test_admin_audit |
| T18 | 跨租户 u7 读任何上述对象 | 404 | 同上 |
| T19 | grep 断言：ROLE_HIERARCHY/require_role/min_level 全库为零 | 通过 | test_capability_decorators（新） |
| T20 | require_capability("interview_digest") 拒绝 admin | 403 | 同上 |
| T21 | data_source create with credential="x" | 400（方案 A 拒绝） | test_data_source_contract（改） |
| T22 | data_source revoke | 200 + admin_action_logs 行（outcome=success, reason 脱敏） | test_admin_audit（新，DB） |
| T23 | revoke 时审计写失败（注入故障） | 业务回滚（HTTP 5xx，无 revoke 效果） | 同上 |
| T24 | triage 时审计写失败（注入故障） | 200 成功 + 告警计数 + ready 暴露 audit_degraded | 同上 |
| T25 | weekly_report publish | 200 + 审计行 | 同上 |
| T26 | GET /api/audit/admin-actions（admin） | 200 分页 | 同上 |
| T27 | GET /api/audit/admin-actions（hrbp） | 403 | 同上 |
| T28 | GET /api/audit/admin-actions 无 DB | 503（非空列表） | 同上 |
| T29 | 经理无任何 user_scope_grants | work-summaries 仅返回自己条目（不 fail-open 为全租户） | test_work_summary_acl |
| T30 | 回填脚本 dry-run：种子 17 条 data_sources + 无主任务 | 全部标 owner_unassigned，无任何行归给 admin/全体 | backfill 测试（新） |

RED 顺序：先写 T1/T2/T11/T12/T19/T21（现有代码必红的越权断言），确认红 → Group1/3/4/7 实现 → 绿；再补 DB 型用例。

---

## 8. 修复后验收流程（全栈重启 + 四角色冒烟 + 证据归档）

### 8.1 全栈重启（旧 8001 证据作废，P1-4）

```powershell
# 1. 停遗留实例（审查已确认 PID 15104/54888 为旧构建）
Stop-Process -Id 15104,54888 -Force -ErrorAction SilentlyContinue
# 2. 迁移（批准后）
docker exec hrbpilot-postgres-1 psql -U postgres -d hrbp_workbench -c "\i rehearse-check"   # 演练库先过
alembic upgrade head        # 对开发库；生产另议
python app/data/migrations/backfill_012_owner.py --dry-run   # 人工核对报告
python app/data/migrations/backfill_012_owner.py --apply
# 3. 从当前工作树启动
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001   # 同端口，明确"新实例"
.venv\Scripts\python.exe -m celery -A app.shared.celery_app.celery_app worker --loglevel=info --pool=solo -Q celery
# 4. 前端
cd web; pnpm dev   # 5173
# 5. 验证运行的就是新代码：openapi 路径列表 + /api/ready + 一个已知修复的行为探针
curl http://localhost:8001/api/work-summaries -H "Authorization: Bearer <employee-token>"  # 必须 403
```

### 8.2 测试命令

```powershell
# 后端
.venv\Scripts\python.exe -m pytest tests -q          # 环境缺 aiosqlite/openpyxl 的既有 25 errors 不新增
.venv\Scripts\python.exe -m pytest tests/shared -q    # 本方案全部新测试所在
.venv\Scripts\python.exe -m ruff check app tests
.venv\Scripts\python.exe -m mypy app
# 前端
cd web; pnpm test:run; pnpm exec eslint .; pnpm exec tsc -b; pnpm build
```

### 8.3 四角色浏览器复测（与独立审查报告 §3.2 矩阵逐条对应）

用 gstack browse 或 Playwright 脚本，每角色登录走查并截图到 `_audit_outputs/post-remediation-2026-XX-XX/screens/`：
1. **employee**：/policy 正常；GET /api/work-summaries → 403 探针；/my-requests 只见自己；
2. **hrbp(u2)**：今日工作只含自己条目（对照修复前 6 条全租户）；/hr-requests 只见指派给自己的；
3. **hr_manager(mgr-A)**：团队待处理含 org-1 + 待分派；/knowledge 正常；/evaluation 仍 403；
4. **hr_manager(mgr-B)**（新增测试用户）：确认 org-2 隔离；
5. **admin**：/admin、/data-sources（凭据输入已变说明文案）、新 /audit 页有记录（含刚才 revoke 产生的行）、GET /api/work-summaries → 403；
6. 每步 console 无错误；截图+命令+响应三件套归档，形成 `_audit_outputs/post-remediation-2026-XX-XX/report.md`（模板=独立审查报告的矩阵表）。

### 8.4 证据归档清单

迁移前快照 SQL、alembic 输出、回填 dry-run+apply 报告、pytest/ruff/mypy/vitest/eslint/tsc/build 全量输出、四角色截图、API 探针响应、`git log --oneline` 分组提交列表。

---

## 9. 需用户批准的事项

### 9.1 数据库 schema 变更（单独列出，等待逐项批准）

| # | 变更 | 影响数据 | 风险 |
|---|---|---|---|
| D1 | 新表 org_units（含每租户默认根行 INSERT） | 新数据 | 低 |
| D2 | 新表 user_scope_grants | 新数据 | 低 |
| D3 | 新表 admin_action_logs | 新数据 | 低 |
| D4 | async_tasks +3 列（owner/assigned/unassigned）| 既有行默认 NULL/FALSE | 低（nullable，不锁表） |
| D5 | weekly_reports +3 列 | 同上 | 低 |
| D6 | employee_requests +org_unit_id +owner_unassigned（激活既有 hr_owner_id） | 同上 | 低 |
| D7 | knowledge_feedback_candidates +2 列 | 同上 | 低 |
| D8 | culture_contents/interview_digests/insight_reports +2 列 | 同上 | 低 |
| D9 | **users 表加 org_unit_id 列** | 用户归属需管理员手工配置 | 中（users 是认证核心表；nullable+无默认，不影响登录路径） |
| D10 | 回填脚本对无主历史行写 owner_unassigned=TRUE | 17+ 条种子 + 若干任务 | 低（只读推断+人工核对 dry-run） |

**批准动作**：批准 D1–D10 整体 / 逐项勾选 / 要求修改。批准后执行顺序：演练库 → 快照 → 开发库 → 回填 dry-run 人工核对 → apply。

### 9.2 实施方案本身

- Group 1–8 的顺序与分组是否认可；
- §3.4 的分诊决策（hrbp 只见指派给自己的；未指派仅经理可见元数据并可分派）是否符合业务预期；
- §3.5 的刻意分层（文化/KB 本轮只换 capability 断言不建对象 ACL）是否接受；
- §5 方案 A（禁存凭据）为推荐项是否认可；
- 新增测试用户 mgr-B 与第二个 HRBP u3 的种子创建方式（回填脚本附带或手工 SQL）。

### 9.3 明确不需要批准的

Group 1（纯代码 capability 重构，无 schema）、Group 7（删 XOR）、Group 8 前端小改、全部测试编写——这些是常规代码工作，随实施提交走正常 review。

---

## 10. 风险与未尽事项

1. **org 子树过滤的完整实现**依赖 D9（users.org_unit_id）且需要真实组织数据；当前租户无组织数据，经理范围在数据补齐前实际等于"本人条目+未指派元数据"——这是诚实的 fail-closed 而非缺陷。
2. 周报 `/sources` 过滤后，经理生成周报时是否能引用 HRBP 下属的分析结果（组织语义"团队周报"）——《方案》§3.2 周报="自己或被授权范围"，完整语义需 D9+授权数据；第一阶段经理只能用自己名下 source（同 hrbp），团队聚合周报列入 backlog。
3. `user_scope_grants` 的管理 UI（授权/撤销界面）本轮只提供 API+审计，UI 归 P2-8 用户与权限页。
4. 旧 8001 实例停止后，审查期间发现的"SSE 空正文"证据链（DB 中 len=0 行）保留原状——它们是历史事实，不清理、不修复（不属于本轮 scope；新实例不产生新空行即验证 P1-4 修复）。
5. 本方案不处理独立审查报告的 P2-2（字体）、P2-3（宣言）、P2-11/12（a11y）——发布阻塞判定只覆盖权限/数据/审计/凭据；其余 P2 维持"发布前应修"建议但不阻塞（除 P2-1 已按用户指示升级为阻塞项纳入本方案）。

---

*本方案到此为止。不写代码、不执行迁移。等待用户对 §9.1 数据库变更与 §9.2 实施方案的批准。*
