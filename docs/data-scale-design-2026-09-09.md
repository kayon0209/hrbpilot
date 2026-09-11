# 工作材料模块 · 大数据量存储与展示设计方案

> 场景假设：调研 500 名员工，面谈纪要与员工声音两个模块的数据存储、页面组织、展示方式、性能风险评估、按员工快速检索的设计。
> 结论先行：**500 人规模本身不是性能问题（MB 级数据，PostgreSQL 毫无压力），真正的问题是当前数据模型没有「员工」维度、原始语料不落库、接口无分页——这是产品形态问题，P0 是数据建模，不是性能优化。**

---

## 一、现状盘点（基于真实代码）

| 层 | 现状 | 证据 | 问题 |
|---|---|---|---|
| 存储 | 面谈/声音**无独立业务表**，分析结果整包 JSON 存 `async_tasks.result_json`（Text 列） | `app/data/models/infra.py:34` | 无法按员工/部门/风险筛选；无员工实体表（全库无 employees 表） |
| 原始语料 | **不落库**——analyze 只把 content 传给 Celery，AsyncTask 无 input 列 | `app/access/routes/interview_digest.py:140`、`infra.py` | 不可追溯、不可重分析、不可检索原文 |
| 接口 | history 一次返回 20 条**完整结果 JSON**，limit 参数无游标、无过滤 | `interview_digest.py:196-247` | 结果大时响应膨胀（20×完整结果可能数百 KB） |
| 前端 | 只取最近一条展示：`history.data?.digests?.find(d => d.result)`；无列表、无分页、无搜索 | `InterviewDigestPage.tsx:11`、`VoiceInsightPage.tsx:21` | 历史记录功能形同虚设，无法承载材料库形态 |

**判断：当前页面是「一次性工具」（粘贴→分析→看完即走），不是「材料库」。500 人调研的数据形态是多条、可检索、可聚合的库，模型必须先改。**

---

## 二、数据量级估算（先算账再设计）

500 名员工的真实体量：

| 数据 | 估算 | 说明 |
|---|---|---|
| 面谈纪要原文 | 500 × 2000 字 ≈ 3 MB（UTF-8 中文 3 字节/字） | 即使 5000 字/篇也只有 ~7.5 MB |
| 员工声音条目 | 500 × 200 字 ≈ 0.3 MB | 问卷开放题/意见反馈 |
| 结构化分析结果 | 每份 2–5 KB JSON → 500 份 ≈ 1–2.5 MB | demands/action_items/risk 等 |
| **合计** | **< 15 MB** | PostgreSQL 单表千万行级都无压力，这个量级连「大数据」的边都不沾 |

**诚实的结论：500 人规模下，数据库查询、存储都不会出任何问题。会出问题的只有三处（见第五节）：接口全量返回、前端无分页全渲染、批量分析的异步任务吞吐。**

---

## 三、存储方案（P0：数据建模）

### 3.1 新增实体表（迁移 021）

```
employees（员工维度，当前完全缺失）
├─ tenant_id, employee_no(工号), name, org_unit_id → org_units
├─ status(在职/离职), tags_json
└─ 索引：(tenant_id, employee_no) 唯一、(tenant_id, name)

interview_records（面谈纪要原始记录）
├─ tenant_id, employee_id → employees, interview_type(绩效/离职/入 职/沟通)
├─ interview_date, interviewer_id → users(created_by)
├─ raw_text_uri → MinIO 对象 URI（原文不入 PG）
├─ raw_text_sha256, raw_text_length（校验+预览用）
├─ digest_task_id → async_tasks（关联分析任务）
└─ status(待分析/分析中/已完成/失败), created_at

voice_entries（员工声音原始条目）
├─ tenant_id, employee_id（可 NULL——匿名调研）
├─ channel(调研问卷/意见箱/座谈会), collected_at
├─ raw_text（短文本可直接 Text 落库）
└─ digest_task_id, batch_id（哪次批量收集）

digest_results（分析结果摘要层——从 JSONB 拆出可筛选字段）
├─ tenant_id, record_type(interview/voice), record_id
├─ risk_level, confidence, summary（列——用于筛选/排序/列表展示）
├─ full_result_json（完整结果 JSONB——详情按需加载）
└─ 索引：(tenant_id, record_type, risk_level)、(tenant_id, created_at)
```

### 3.2 存储原则：三层分离

> **实现调整（诚实说明）**：本期落地了 employees / interview_records / voice_entries 三表与原文落库；`digest_results` 摘要物化表暂缓——列表接口改为**服务端** JOIN async_tasks 并解析 result_json 摘要字段后返回（解析发生在服务端 20 条上，前端仍只拿摘要）。500–5000 条规模下该方案毫秒级；当单租户记录过万、或列表需要按风险等级/摘要排序筛选时，再启用摘要物化表（届时列表查询从「JOIN+解析」改为「直读摘要列」）。

1. **原文层** → MinIO 对象存储（后端已有），DB 只存 URI + sha256 + 长度。优点：PG 表保持瘦、备份快、原文不可篡改审计友好。
2. **摘要层** → 结构化列（risk_level/summary/confidence），直接支撑列表页、筛选、排序，**列表查询永不碰大 JSON**。
3. **结果层** → 完整分析结果 JSONB，仅在用户点开详情时按主键取单条。

### 3.3 必须遵守的既有约束（踩过的坑）

- 新表**从迁移第一天就进 FORCE RLS 清单 + tenant 复合 FK**——020 迁移已为「漏表导致 FK 校验误报」付出过修复成本（`020_tenant_composite_fk.py` 现在自动从 `pg_class.relforcerowsecurity` 发现，但新表仍应一开始就声明）。
- 沿用 `TenantMixin + UUIDPrimaryKey + TimestampMixin` 与既有 FK 命名规范。
- 语义红线（项目 ADR）：不做图谱、不做多 agent，检索用「索引 + SQL/全文」即可，不引入向量库（员工材料检索是结构化+关键词场景，不是语义召回场景）。

---

## 四、页面组织与展示方案

### 4.1 信息架构：从「工具页」升级为「材料库页」

```
┌──────────────────────────────────────────────────────┐
│ 面谈纪要                                    [+ 新建分析] │
│ ┌──────────────────────────────────────────────────┐ │
│ │ 🔍 搜索员工姓名/工号/关键词          [部门▾][风险▾][时间▾] │ │
│ └──────────────────────────────────────────────────┘ │
│ ┌──────────────────────────────────────────────────┐ │
│ │ 列表行：员工姓名 · 部门 · 访谈日期 · 风险徽标 · 摘要一行 │ │
│ │ 列表行：…（每页 20–50 条，cursor 分页）               │ │
│ └──────────────────────────────────────────────────┘ │
│ 点开行 → 右侧抽屉：完整结构化纪要（按需加载 full_result_json）│
└──────────────────────────────────────────────────────┘
```

- **列表只渲染摘要层字段**（姓名/日期/风险/一行摘要），接口不返回 full_result_json。
- **详情懒加载**：点开才请求 `/records/{id}` 取完整结果——这是接口瘦身的关键配套。
- 现有「粘贴→分析」入口保留为右上角「新建分析」，分析完成后自动落库并跳到该记录详情。

### 4.2 员工声音的聚合视图

声音模块天然是「多对一聚合」场景，列表之上加一层聚合看板：

- **主题聚类 Top N**、情绪分布、风险等级占比——聚合指标**预计算**：批量分析完成时顺手写 `voice_aggregates` 汇总表（或定时物化视图），前端只读汇总，绝不在浏览器里对 500 条明细 reduce。
- 明细列表按 `collected_at` 倒序 + 渠道筛选。

### 4.3 分页策略

- **Keyset（cursor）分页**：`WHERE (created_at, id) < (:last_cursor) ORDER BY created_at DESC, id DESC LIMIT 50`。避免 OFFSET 深翻页随页码线性变慢。
- 前端「加载更多」按钮即可，500 条不需要虚拟滚动；若未来单租户过万条再引入 `react-window` 虚拟列表。

---

## 五、性能与异常风险评估（诚实分级）

| 风险点 | 500 人时是否触发 | 说明 | 级别 |
|---|---|---|---|
| 数据库查询变慢 | **否** | <15 MB 数据 + 复合索引，毫秒级 | 无 |
| history 全量结果返回 | **是** | 20 条 × 完整结果 JSON，数百 KB 响应拖慢首屏 | **P0**：接口改摘要列表 |
| 前端无分页全渲染 | **是（若直接铺 500 行）** | 500+ DOM 节点卡顿、React reconciliation 变慢 | **P1**：cursor 分页 |
| 批量分析吞吐 | **是（最大真实瓶颈）** | Celery `--pool=solo` 并发=1，每条 LLM 分析 ~10s → 500 条 ≈ **1.5 小时排队**；且单条失败会怎样需设计 | **P1**：批量任务机制 |
| 系统异常/崩溃 | **否** | RLS 租户隔离完备、上传有 20MB 限制、任务有过期清扫（`expire_stale_tasks`） | 无 |

**批量分析任务设计（数据量大时唯一需要认真对待的后端机制）：**
- 一次提交 = 一个 batch 任务，内部拆 500 个子项，`progress` 按子项递增（1/500 → 前端能显示真实进度而非假百分比——符合项目「stage words, never fake percentages」原则）。
- 子项级失败隔离：单条 LLM 解析失败只标记该条 `failed`，可单独重试，不整体重来。
- token 成本预估入 token_ledger（已有机制），500 条 × ~3k tokens ≈ 150 万 tokens/轮，租户月预算（默认 1000 万）内可跑 ~60 轮，需在批量提交前展示预估并确认。

---

## 六、按员工快速查找：检索 + 交互

### 6.1 检索方式分层（500 人规模，不用外部搜索引擎）

| 需求 | 方案 | 备注 |
|---|---|---|
| 按姓名/工号找人 | `employees` 表 btree 索引精确/前缀匹配；模糊用 pg_trgm GIN 索引 | 毫秒级 |
| 组合筛选（部门+风险+时间） | digest_results 复合索引 | 毫秒级 |
| 纪要正文关键词 | PG tsvector + GIN 全文索引 | 中文分词选型：优先应用层 jieba 预分词后写 tsvector（不依赖 zhparser 扩展，Docker 镜像不用换）；500 篇文档量级 GIN 足够 |
| 万级以上规模 | 才考虑 Meilisearch/ES | **500 人不需要，不过度设计** |

**不用向量检索的理由**：按员工找材料是「精确实体 + 关键词」场景，不是语义模糊召回场景；且项目 ADR 反对默认引入图谱/向量重基建。

### 6.2 交互设计

1. **顶部搜索框**：输入即搜（debounce 250ms），下拉分组「员工 / 面谈纪要 / 声音条目」，回车进详情。
2. **键盘可达**：`/` 快捷键聚焦、↑↓ 选择、Enter 打开——HR 高频操作场景必备。
3. **命中高亮**：后端 `ts_headline` 或前端对命中词包 `<mark>`。
4. **筛选与搜索可叠加**，状态同步到 URL query（可分享、可回退、刷新不丢）。
5. **空结果**给「放宽筛选」建议（如「试试去掉风险等级筛选」），不白屏。
6. **重名消歧**：同名员工在结果里带工号 + 部门后缀。
7. **匿名声音**：employee_id 为空的条目单独「匿名调研」分组展示，不强行归人。

### 6.3 安全注意（不可妥协）

- 搜索接口**必须延续 `resolve_visible_user_ids` 数据可见范围过滤**——员工材料涉及个人信息，搜索不能成为越权通道（现在 history 已按 visible_user_ids 过滤，新检索接口同样必须）。
- 搜索结果中的原文摘录走脱敏管道（项目已有 InputGuardrail/PII 脱敏环节，检索输出也要过）。
- audit_logs 记录「谁在何时查了哪位员工的材料」，高频查询敏感员工材料应有审计可见性。

---

## 七、落地路线

| 级别 | 内容 | 状态（2026-09-09 晚） |
|---|---|---|
| **P0** | 数据建模：employees / interview_records / voice_entries + 迁移 035（含 FORCE RLS + 复合 FK + 索引） | ✅ 已实现：`app/data/models/material.py` + `035_material_library.py`，已在本地库执行 |
| **P0** | analyze 时原文落库 + 关联分析任务 | ✅ 已实现：interview/voice analyze 均持久化原始语料与 task 关联（best-effort，失败不阻塞分析） |
| **P0** | history 改摘要列表 | ✅ 已实现：新增 `GET /records`、`GET /entries`（cursor 分页 + q 搜索，列表只返回摘要字段）；旧 `/history` 契约保留兼容（测试锁定） |
| **P0** | 详情按需加载 | ✅ 已实现：`GET /records/{id}`、`GET /entries/{id}` 返回完整结果；前端点行展开时才请求 |
| **P1** | 前端材料库改版（搜索 + 分页列表 + 详情展开 + 新建分析带员工/标题/日期/渠道元数据） | ✅ 已实现：InterviewDigestPage / VoiceInsightPage 重写为「分析区 + 历史材料区」 |
| **P1** | 批量分析任务（子项进度 + 失败隔离 + token 预估确认） | ⏳ 未实现（500 条批量提交场景，需先有批量导入入口） |
| **P2** | 全文检索（jieba + tsvector + GIN）+ 高亮/键盘快捷键；员工声音聚合看板 | ⏳ 未实现（当前 ILIKE 在 500–5000 条规模足够） |

**不建议现在做的**：向量库/搜索引擎引入、微服务拆分、前端虚拟滚动——在 500–5000 人规模都是过度设计。

### 实现验证记录（真实运行）
- 迁移：`alembic upgrade head` 034 → 035 成功。
- 后端：pytest 398 passed / 49 skipped（全量，含 22 条相关路由/ACL 用例）。
- 前端：vitest 37/37（13 文件全绿，`--maxWorkers=2`；默认并发会因本机内存不足崩溃 worker，属环境问题）；`tsc --noEmit` 0 错误；`vite build --minify false` 成功。
- E2E（真实 LLM + Celery，Playwright）：面谈流——提交带员工姓名/日期的分析 → interview_records 落库 → 列表出风险徽标 → 点行展开完整纪要 → 按姓名搜索命中 → 无结果空态正确；员工声音流同链路复验通过（渠道徽标、严重度、完整洞察报告渲染正常）。
- 排障记录（两条重要运维结论）：
  1. **Celery worker 必须单实例**：发现新旧会话遗留的两个 worker 进程并存，新任务消息被未正常注册的僵尸进程吞掉、任务永久 pending（消息未被消费也未入队，`redis-cli LLEN celery = 0` 但任务无进展）。处置：kill 全部 worker → 单实例重启 → 任务正常完成。排查命令：`celery inspect stats` 看 total 是否增长、`celery inspect ping` 看在线节点数。
  2. **限流器会拦测试脚本**：`/api/*` 有单用户限流，E2E 高频轮询（进度 + 列表）耗尽配额后连 analyze 都会 429、会话校验被拦导致页面跳回登录。表现为「提交失败/列表为空」，实为 429——压测/自动化脚本需控制请求密度，前端表现已正确区分（role=alert 错误提示）。
- 测试残留已清理（interview_records / voice_entries / employees / async_tasks / insight_reports 同步清空）。

---

## 附：「下一步」字段说明（任务表单）

「下一步」不是最终目的，是 GTD 的 **Next Action**：任务当前推进到的、**动词开头的单个可执行动作**（例：「联系候选人确认入职材料」）。最终目的是「任务名称」（例：「完成张三的入职」）。设计意图：HR 打开任务卡不需要回忆上下文，看一眼就知道现在该干什么，与「等待对象」「截止时间」共同构成推进状态。

原实现「差点意思」的根因是输入框没有任何引导（无 placeholder、无 hint），用户不知道该填什么粒度。已修复：新建与编辑表单的「下一步」输入框补了 hint「填具体动作，不是最终目标」和示例 placeholder。可选进一步改名：「下一步动作」（更明确）或「当前推进」（更弱化步骤感），涉及任务卡、回顾页等 4 处文案，待定后可一次改齐。
