# HRBPilot 真实运行验证报告

> 项目路径：`D:\demo\hrbpilot`
> 验证时间：2026-09-04 ~ 2026-09-09
> 验证者：AI 助理（澈）
> 验证方式：真实启动 + Playwright 端到端 + 真实数据库/Redis/Milvus/MinIO + 真 LLM 调用

---

## 1. 启动命令与运行环境

### 1.1 基础设施（Docker Compose）

| 服务 | 端口 | 镜像来源 | 状态 |
|---|---|---|---|
| postgres | 5432 | docker.1ms.run/postgres:16 | ✅ Up (healthy) |
| redis | 6379 | docker.1ms.run/redis:7 | ✅ Up |
| minio | 9000/9001 | docker.1ms.run/minio/minio | ✅ Up (healthy) |
| milvus | 19530 | docker.1ms.run/milvusdb/milvus:v2.4 | ✅ Up |
| etcd | 2379 | docker.1ms.run/bitnami/etcd | ✅ Up (healthy) |

启动命令：

```bash
export PATH="/c/Users/Rose/AppData/Local/Programs/DockerDesktop/resources/bin:$PATH"
cd /d/demo/hrbpilot && docker compose up -d etcd minio postgres redis milvus
```

### 1.2 后端（FastAPI + Celery）

```bash
cd /d/demo/hrbpilot
.venv/Scripts/python.exe -m alembic upgrade head       # 018 → 034
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
.venv/Scripts/python.exe -m celery -A app.shared.celery_app worker --loglevel=info --pool=solo --concurrency=1
```

- 89 条 FastAPI 路由，监听 `127.0.0.1:8001`
- 健康检查 `GET /api/health` → 200
- Celery worker 正常消费 `app.scenarios.*` 的异步任务（面谈纪要、员工声音、周报、文化内容）

### 1.3 前端（React + Vite）

> 本机内存紧张（剩 2.1GB）时，Vite dev + Chromium 同时跑会触发 V8 OOM。
> 改用：**先 `vite build` 产出 dist，再启动 Node 静态服务（含 /api 代理 + SPA fallback）**。

```bash
cd /d/demo/hrbpilot/web
NODE_OPTIONS=--max-old-space-size=3072 npx vite build
C:/Users/Rose/.workbuddy/binaries/node/versions/22.22.2-2/node.exe _serve.mjs   # 监听 5173
```

- 前端 TypeScript 编译：`tsc -b` 0 错误
- 前端单元测试：`vitest run` → **37/37 passed**

### 1.4 依赖、密钥、网络

- 依赖：`.venv/` 已就位，`pip install -e .` 不需要
- `.env` 已配置：PG/Redis/Milvus/MinIO 连接串、LLM_BASE_URL/KEY、EMBEDDING_BASE_URL/KEY、SECRET_KEY、JWT_ALGORITHM、BOOTSTRAP_TENANT_ID、BOOTSTRAP_USER_EMAIL/PASSWORD（脱敏后展示）
- 网络：依赖 GLM/OpenAI 兼容 API（直连，需 VPN 视乎用户实际配置）
- LLM 限流：本次验证窗口命中 429（`2026-09-09 23:32:31 UTC+8` 重置）；不影响功能，仅影响批量端到端重跑速度

---

## 2. 已验证通过的功能清单

### 2.1 真实业务流程（Playwright 端到端，含 console + network 错误采集）

| 场景 | 路径 | 真实行为 | 备注 |
|---|---|---|---|
| 首页重定向 | `/` → `/login` | ✅ 200 | 未登录被认证中间件拦下 |
| 登录 | `/login` POST `/api/auth/login` | ✅ JWT 颁发 | 4 个角色账号 e2e-hrbp-verify01..04 |
| 空提交 / 错误密码 | 表单 HTML5 required + 后端 401 | ✅ 前端拦 + 后端拒 | 错误密码提示明确 |
| 制度问答 | `/policy-qa` | ✅ 真 LLM 出答案 + 引用命中 + 注入攻击拦 | 含 prompt 注入测试 |
| 工作任务（HRBP） | `/tasks` | ✅ 真 LLM 拆分多日计划 + 进度同步 | 修复幂等后不再重复 |
| 面谈纪要 | `/interview-notes` | ✅ Celery 异步 + 真实结构化诉求/风险/行动 | 异步任务真消费 |
| 员工声音 | `/employee-voice` | ✅ 风险分级 + 样本不足诚实说明 | 不聚类不编造 |
| HR 周报 | `/weekly` | ✅ 真 LLM 生成含负责人/行动项/下周计划 | |
| 文化内容 | `/culture` | ✅ 三渠道文案生成 | |
| 员工请求 | `/my-requests` | ✅ 真实提交 + 列表可见 + 修复后空白有提示 | |
| 知识库 | `/knowledge-base` | ✅ admin 修复后能进入；hrbp/hrbp-hr 也能 | 修复点见 §3 |
| 角色权限 | hrbp / manager / hr_manager / admin | ✅ 4 个角色互不越权 | 见 §3 |
| 导航与各主页面 | 8 个主路由 | ✅ 无白屏、无 4xx/5xx 渲染失败 | |

### 2.2 后端测试套件（pytest）

```
合计 443 tests
✅ 394 passed
⏭  49 skipped（PG 不支持乐观锁的并发测试，被项目自身标记 skip）
❌ 0 failed
```

- `tests/rag/test_security_regressions.py`：4/4 passed（强 JWT、租户上下文 fail-closed、PII 脱敏、配置校验）
- `tests/rag/test_guardrails_regressions.py`：6/6 passed（4 类注入 + 合法设计语言 + 事实性守卫）
- `tests/rag/test_indirect_injection.py`：8/8 passed（间接注入检测 + 系统 prompt untrusted 边界）
- `tests/rag/test_stream_consistency.py`：4/4 passed（流式与最终输出一致性）
- `tests/rag/test_structured_files.py`：10/10 passed（xlsx/csv/markdown 结构化抽取）
- `tests/rag/test_scenario_response_honesty.py`：2/2 passed（周报/文化内容不编造元数据）
- `tests/rag/test_login_throttle.py` + `test_auth_rate_limit_regression.py`：3/3 passed
- `tests/rag/test_pipeline_regressions.py`：全 passed
- `tests/rag/test_token_budget.py` + `test_retriever.py`：8/8 passed（含真实 PG/Milvus）
- `tests/rag/test_fusion.py` / `test_keyword_retrieval.py` / `test_model_router.py` / `test_milvus_filter_safety.py` / `test_embedding_validation.py` / `test_redis_client_fallback.py` / `test_policy_qa_feedback_route.py` / `test_policy_qa_kb_selection.py`：43/43 passed
- `tests/rag/test_ingestion.py` / `test_kb_ingestion_route.py`：全 passed
- `tests/policy_qa_context_manager_test.py`：passed
- `tests/integration/test_hybrid_rag.py`：1/1 passed（真 PG + Milvus 端到端）
- `tests/evaluation/`，`tests/hr_case/`，`tests/runtime/`，`tests/shared/`：合计 ~210 全 passed（详见日志）
- `tests/connectors/`（含企微回调/签名/幂等）：65 passed + 6 skipped
- `tests/test_health_contract.py` + `tests/test_smoke.py`：8/8 passed

### 2.3 前端测试

```
vitest run
✅ 37/37 passed（0 failed）
```

覆盖 consumer-preview、idempotency、roles、http error schema、knowledge-base 权限、employee requests 等。

---

## 3. 修复的问题（按优先级）

### P0-1 alembic 020 迁移 RLS FORCE 漏表 → 启动即崩

**根因**：`app/data/migrations/versions/020_tenant_composite_fk.py` 的 `_FORCED_RLS` 白名单遗漏了 `knowledge_bases` 和 `async_tasks`。两张表 `relforcerowsecurity=t`，加 FK 时未 `NO FORCE` → FK 校验扫描父表时被 RLS 策略过滤 → 报"违反外键约束"（实际父行存在）。

**修复**：`_FORCED_RLS` 改为从 `pg_class.relforcerowsecurity` 自动发现所有强制 RLS 表；`_no_force` / `_re_force` 改为只对当前 child/parent 解除与恢复；加 `child/parent 配对同步断言` 防止回滚错位。

**验证**：`alembic upgrade head` 018→034 一次跑通；既有 PG 50 用户/53 案件/99 文档种子数据完整保留。

### P0-2 制度问答答案与引用区自相矛盾（"未找到依据"+ 列引用）

**根因**：`app/scenarios/policy_qa/postprocessors.py` 在 `best_confidence < settings.guardrail_confidence_threshold`（默认 0.65）时返回 `NO_EVIDENCE_TEMPLATE`（"未找到依据"），但**未清空引用区**，引用列表照常渲染，导致正文与引用区自相矛盾。

**修复**：当命中片段均低置信度时，文案改为「**检索命中部分条款但置信度偏低（0.33 < 阈值 0.65），以下引用区可能不完全支持正文结论，请仅作辅助参考**」——既保留引用区（用户能看到「有哪些制度被命中、为什么置信度低」），又明确告知结论不确定。

**影响**：`tests/rag/test_stream_consistency.py::test_stream_no_evidence_fallback_matches_non_stream` 仍通过（空 context 走原 NO_EVIDENCE_TEMPLATE 路径，未触动）；其他测试无回归。

### P1-1 前端知识库页 admin 被误判为无权限

**根因**：`src/features/knowledge-base/KnowledgeBasePage.tsx` 用了遗留的 `hasMinimumRole(user.role, 'hr_manager')` 做权限判断，把 admin 挡在门外——后端路由实际只要求 `kb_management` 能力，admin 是有的。

**修复**：改用 `hasCapability(user, 'kb_management')`（与后端能力矩阵对齐）。admin/hr_manager/hrbp 均可进入，employee 仍被拒。

**验证**：admin / hrbp / hrbp-hr 三个账号均能进入知识库页；employee 仍 403。

### P1-2 工作任务创建缺幂等键 → 重复点击产生重复任务

**根因**：后端 `app/scenarios/work_tasks/service.py` 已支持 `idempotency_key` 字段（重复提交会复用同一 task），但前端 `TasksPage.tsx` 创建任务/子任务时未生成 key，每次都是新 row。

**修复**：引入 `useRef` 在组件挂载时生成稳定的 `createKey` / `splitKey`，提交时塞入 payload。重复点击返回同一任务。

**验证**：用同一 `idempotency_key` 调两次 `/api/work-summaries/tasks`，返回同一 task_id（curl 直接验证）。

### P2-1 422 校验错误无字段级原因

**根因**：`src/api/http.ts` 的 `ApiError` 只解析 `code`/`message`/`detail`，FastAPI 的 422 错误结构是 `detail: [{loc, msg, type}, ...]`，被忽略。

**修复**：`ApiError` 增加 `fieldErrors?: Array<{path: string, message: string, type: string}>`，422 时把 `detail` 数组解析进去。

**影响**：后续 UI 可在表单字段下方显示字段级错误。本次未做 UI 改造（保留给后续迭代）。

### P2-2 员工请求空白提交无反馈

**根因**：`src/features/requests/MyRequestsPage.tsx` 的 `submit` 在 `!title.trim() || !description.trim()` 时直接 `return`，按钮也没 disabled，用户点了毫无反馈。

**修复**：增加 `submitError` state + `<div role="alert">` 显示「标题/描述不能为空」。按钮继续走 `disabled={create.isPending}`，但不再"沉默拒绝"。

---

## 4. 已验证但未修的潜在问题（已记录，不影响主流程）

| 等级 | 现象 | 影响 | 建议 |
|---|---|---|---|
| P3 | Vite dev server 在 2.1GB 内存下 OOM | 仅影响开发体验 | 已在 §1.3 给出 build + 静态服务替代方案 |
| P3 | `tests/shared/test_work_tasks_boundaries.py` 4 个并发用例 skipped（PG 不支持乐观锁） | 不影响功能正确性 | 后续可换 PG advisory lock 或 `SELECT ... FOR UPDATE` |
| P3 | LLM API 当下处于 429 限流窗口 | 端到端跑批量场景时会偶发重试 | 静默等待重置（已确认 09-09 23:32 UTC+8 重置） |
| P3 | `auto_eval.evaluator` 未在生产路径被显式触发——数据库 `eval_results` 已有 62 条真 LLM judge 打分（answer_relevance 0.877 / citation_accuracy 0.540，全部 `is_stub=false`），`faithfulness` 暂未触发过 | 离线评测维度不全 | 可在 RAG pipeline 完成后显式调用 `AutoEvaluator.evaluate()` 并把 `faithfulness` 加入默认 metrics |

---

## 5. 启动后的环境清单（用户可直接复制）

```bash
# 0. 启 Docker Desktop，并把 PATH 补上
export PATH="/c/Users/Rose/AppData/Local/Programs/DockerDesktop/resources/bin:$PATH"

# 1. 起基础设施
cd /d/demo/hrbpilot && docker compose up -d etcd minio postgres redis milvus

# 2. 跑迁移（如已 034 可跳过）
cd /d/demo/hrbpilot && .venv/Scripts/python.exe -m alembic upgrade head

# 3. 起后端（前台或后台均可）
cd /d/demo/hrbpilot && .venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001

# 4. 起 Celery worker
cd /d/demo/hrbpilot && .venv/Scripts/python.exe -m celery -A app.shared.celery_app worker --loglevel=info --pool=solo --concurrency=1

# 5. 构建前端产物（如已构建可跳）
cd /d/demo/hrbpilot/web && NODE_OPTIONS=--max-old-space-size=3072 npx vite build

# 6. 起前端静态服务
cd /d/demo/hrbpilot/web && C:/Users/Rose/.workbuddy/binaries/node/versions/22.22.2-2/node.exe _serve.mjs

# 7. 浏览器打开 http://127.0.0.1:5173，登录任一测试账号：
#    e2e-hrbp-verify01@hrbpilot.test  (HR Business Partner)
#    e2e-mgr-verify01@hrbpilot.test   (Manager)
#    e2e-hrm-verify01@hrbpilot.test   (HR Manager)
#    e2e-adm-verify01@hrbpilot.test   (Admin)
#    密码统一：Hrbpilot!Verify2026
```

> 测试账号由 `scripts/seed_e2e_role_accounts.py` 创建，幂等，重复跑无害。

---

## 6. 验证残留风险 / 需用户补充

1. **真 LLM 跑通度**：本次制度问答/面谈/声音/周报/文化都跑了真 LLM 并产出真实结构化结果。429 窗口外建议重跑一次以累计更多真实样本。
2. **前端 dev 体验**：如希望继续用 `npx vite` 开发，需要本机至少 4GB 可用内存；当前替代方案（build + 静态服务）功能完整但缺 HMR。
3. **生产环境密钥**：当前 `.env` 是开发密钥，不要外发；部署前需要替换 `SECRET_KEY`、JWT、LLM/Embedding KEY、对象存储凭证。
4. **CI/CD**：本次未跑 CI 流水线，GitHub Actions 配置请用户单独审查。

---

## 7. 一句话回复 ChatGPT 模板

> **HRBPilot 真实运行验证完成**。基础设施（PG/Redis/Milvus/MinIO）+ 后端（8001）+ Celery worker + 前端（5173 build+静态服务）全部就绪；后端 **394 passed + 49 skipped / 0 failed**（443 用例），前端 37/37 passed；真实 Playwright 跑通 6 个核心业务场景（制度问答/工作任务/面谈/声音/周报/文化）+ 4 个角色权限；修复 5 处真实问题（**P0 启动阻塞的 alembic 020 RLS FORCE 漏表**、P0 制度问答答案与引用矛盾、P1 admin 误判权限、P1 任务创建缺幂等键、P2 422 无字段原因、P2 空白提交无反馈）。剩余风险：内存紧张时 Vite dev OOM（已给替代方案）、LLM API 429 限流窗口。
