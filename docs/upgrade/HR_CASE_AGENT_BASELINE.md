# HR Case Agent 升级基线报告（Phase 0）

> 生成日期：2026-08-27
> 审计 commit：`f594ce6e120404b40bf7f928ecb6c36dd239736b`（main，`fix: add jinja2 runtime dependency`）
> 性质：只读审计，未修改任何业务行为。
> 对应计划：《HRBPilot_Cursor升级执行计划.md》Phase 0。

---

## 1. 环境与工作区

| 项 | 值 |
| --- | --- |
| Python | 3.12.0（满足 `requires-python >=3.12`） |
| 平台 | Windows / Git Bash |
| 分支 | `main` |
| 远程 | `origin git@github.com:kayon0209/hrbpilot.git` |

**工作区不干净（未提交内容，本阶段一律未触碰）：**

- `M .gitignore` —— 新增忽略规则：`_audit_outputs/`、`_*.txt`、`_shot_*.png`、`remove_secret.py`、`replace-secrets.txt`（本地调查产物）
- `?? .ignore`
- `?? AGENTS.md`
- `?? _audit_employee.py`、`?? _audit_full.py`、`?? _audit_smoke.py`（历史审计脚本）
- `?? docs/superpowers/plans/2026-08-17-codex-obsidian-memory.md`

经 `git ls-files` 核实：以上根目录审计产物（`_*.txt`、截图、`replace-secrets.txt` 等）**均未被 git 跟踪**，仅存在于本地并已被（未提交的）`.gitignore` 规则覆盖，不存在仓库泄露。建议尽快把该 `.gitignore` 改动单独提交固化。

## 2. 基线命令与真实结果

| 命令 | 结果 | 分类 |
| --- | --- | --- |
| `python -m pytest` | **62 passed, 1 skipped**（25.58s） | 通过 |
| `python -m pytest tests/integration -q -rs` | `SKIPPED [1] tests\integration\test_hybrid_rag.py:87: Milvus not reachable — start it with 'docker compose up'` | 环境/服务缺失 |
| `python -m ruff check app tests evaluation` | **3 errors** | 当前主分支已有失败 |
| `python -m mypy app` | `Success: no issues found in 107 source files` | 通过 |

失败归因：

1. **当前主分支已有失败（ruff ×3）**：
   - `evaluation/_golden_metrics_obsolete.py:30:54 F821 Undefined name t_list`（废弃文件已损坏但仍在 lint 范围内）
   - `evaluation/run_golden_eval.py:44:1 I001 import 排序`
   - `evaluation/run_golden_eval.py:204:17 F841 局部变量 rec 赋值后未使用`
2. **环境/服务缺失**：集成测试需要 PostgreSQL + Milvus（`docker compose up`）；Redis / MinIO / LLM Key 同理仅在集成或评测运行时需要。离线单测设计良好：外部服务不可用时跳过而非伪造通过。
3. **本次升级导致的失败**：无（尚未修改代码）。

## 3. 架构与模块盘点

技术栈：FastAPI + Pydantic v2 · SQLAlchemy(async)+asyncpg+Alembic(6 个迁移) · Redis · Celery · MinIO · Milvus(pymilvus) · OpenAI/Anthropic SDK · structlog。

```
app/
├── main.py              # create_app + 中间件链 + include_router×9
├── access/              # 认证(JWT)、RBAC 装饰器、租户中间件、全部 HTTP routes
│   └── routes/{auth, policy_qa, interview_digest, voice_insight, weekly_report,
│               culture_content, kb, settings, eval_metrics}.py
├── rag/                 # Hybrid RAG：dense(Milvus) + sparse(PG+jieba) + RRF 融合，
│                        # 19 文件；orchestrator 为生成主入口；无 mock 回退
├── scenarios/           # 五个独立场景编排器 policy_qa / interview_digest /
│                        # voice_insight / weekly_report / culture_content + tasks(Celery)
├── guardrails/          # 输入护栏(注入/PII) + 输出护栏(合规/factuality)
├── evaluation/          # golden_dataset.py(250条) · auto_eval.py(LLM-as-judge)
│                        # · golden_metrics.py · metrics.py
├── data/                # models/ repositories/ migrations/(Alembic v001–006)
├── shared/              # logger(structlog) · errors(AppError) · redis_client
│                        # · token_budget(Redis 月度聚合为主、内存降级)
└── config/              # pydantic-settings
evaluation/              # run_golden_eval.py（离线评测 runner）、results/、summarize_cost.py
tests/                   # test_smoke.py（离线）· rag/*（离线单测/回归）· integration/*
                         # （marker: integration，需 live PG+Milvus）
```

API 面（include_router 于 `app/main.py:89-98`）：health(/api) + auth(login/refresh/me/dev-users) + 5 场景路由 + KB 管理(create/list/upload/ingest/documents…) + settings + eval_metrics。

测试组织：pytest `asyncio_mode=auto`，注册 marker `integration: requires live PostgreSQL and Milvus services`；集成测试以可达性检查显式 skip，不会静默假过。

## 4. Golden 数据集核实结果

- 运行时导入五个集合：`POLICY_QA_GOLDEN=50, INTERVIEW_DIGEST_GOLDEN=50, VOICE_INSIGHT_GOLDEN=50, WEEKLY_REPORT_GOLDEN=50, CULTURE_CONTENT_GOLDEN=50`，**合计 250** —— 与计划断言一致。
- `GoldenSample` 字段：`scenario_id, input, expected_output_contains, expected_citations, expected_risk_level, should_reject, notes`。尚无 `sample_source` / `category` 元数据 —— Phase 1.1 需向后兼容地补充。
- 手写/扩增比例的声明口径问题见 §6-R5。

## 5. 计划断言核实（逐条对到代码）

| 计划断言 | 核实结论 | 证据 |
| --- | --- | --- |
| golden 集 250 条（5×50） | ✅ 属实 | 上节实测导入 |
| AutoEvaluator 失败记 `0.0` 污染趋势 | ✅ 属实 | `app/evaluation/auto_eval.py` 多处 judge 异常/不可解析路径直接 `return 0.0` 或 `_parse_score(raw) or 0.0`（L108/L120/L121/L135/L136/L141/L153 一带），合法 0 分与调用失败无法区分 |
| `run_golden_eval.py` 未 await `record_token_usage()` | ✅ 属实 | `evaluation/run_golden_eval.py:197,204` 两处同步调用 async 函数，返回协程未消费（正是 ruff F841 `rec` 所在行区域） |
| Citation 指标为答案字符串子串匹配 | ✅ 属实 | `app/evaluation/golden_metrics.py::citation_recall` 按 expected 来源名是否出现在 output 判断 |
| token_budget 以 Redis 月度聚合为主、内存降级 | ✅ 属实 | `app/shared/token_budget.py`（非完整不可变账本） |

## 6. 已知风险与修复清单（进入 Phase 1 前）

1. **R1 · ruff 主分支红灯（3 处）**：均为 eval 路径文件，可并入 Phase 1 的 runner/judge 改造一起清理；注意 `_golden_metrics_obsolete.py` 是损坏的废弃文件——处理方式（删除 vs 移出 lint）需明确决策后执行。
2. **R2 · Judge 失败语义污染质量趋势**：Phase 1.2 核心改动；需新增「合法 0 分」「judge 抛异常→skipped」「不可解析→skipped」三类测试。
3. **R3 · token 记账未 await + 结果文件可追溯性缺失**：Phase 1.3；同时补 run_id/commit/数据集哈希/mode 标记与 error_count 不缩分母约束。
4. **R4 · Citation 指标定义过窄**：先写 `docs/evaluation/CITATION_METRIC_SPEC.md` 再动实现（source_recall / source_precision / claim_support_rate / citation_binding_accuracy 四指标分离）。
5. **R5 · 公开指标口径混合**：README §评测结果将 100 条手写样本与 150 条模板扩增样本合成"250 条 golden 集"一个口径对外展示，违反计划 3.1「不得混宣传」；后续文档须分列 hand_authored / parameterized。
6. **R6 · 仓库卫生**：根目录存在大量本地调查产物（未被跟踪）；`replace-secrets.txt`、`remove_secret.py` 名称暗示曾做过密钥轮换——保持其不被提交；建议提交 `.gitignore` 现有改动。
7. **R7 · 远端前端分支未合并**：`codex/frontend-workbench@e56cf22` 待 Phase 3 评审合并。
8. **R8 · mypy 非 strict 模式为有意保留**（pyproject 注释说明：待剩余函数注解完成后开启 disallow_untyped_defs 等）。

## 7. 后续 Phase 的实际文件映射

| Phase | 目标文件 |
| --- | --- |
| 1 | `app/evaluation/golden_dataset.py` · `app/evaluation/auto_eval.py` · `evaluation/run_golden_eval.py` · 新增 `tests/evaluation/test_golden_dataset_contract.py` 等 |
| 2 | `app/scenarios/policy_qa/` · `app/rag/pipeline.py` · `app/rag/retrieval/retriever.py` · 产出 `evaluation/results/policy_qa_error_analysis_<date>.json` |
| 3 | 远端分支 `codex/frontend-workbench@e56cf22` |
| 4–5 | 新增 `app/scenarios/hr_case_agent/`（schemas/state/planner/policy/tools/service/routes）＋ Alembic v007+ |
| 6 | 新增 Agent 轨迹评测集与红队套件（tests/agent_eval 或同构路径） |
| 7 | `app/shared/token_budget.py`（补持久化账本）· 可观测性 · Demo 与文档包 |

## 8. 与计划的差异说明

- 工作区不干净 → 按 Phase 0 规则未 stash/reset/删除，改以只读方式继续审计并列明上述清单（见 §1）。
- 本机未注册 codex-memory 项目记录（工具返回 not registered），不影响基线结论，属可选记忆层。
