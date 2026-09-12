# HRBPilot 修复编号登记表（FIX_NUMBER_REGISTRY）

目的：把代码注释与提交信息里引用的 `P<级别>-<序号>` 编号，登记成**可在仓库内查证的索引**，
让任何一个 `(P1-05)` 注释都能被反查到它修的是什么、证据在哪、由哪个提交落地。

本表是**索引**，不是权威定义。权威定义见各批次报告（见 §2 来源列）。

---

## 1. 为什么需要这张表

本仓库的编号引用存在两个问题，会导致交接时无法追溯：

1. **两套编号体系并存且重号**。`docs/upgrade/2026-08-31_SEAL_FIX_LEDGER.md` 使用 `P0-01`～`P0-05`，
   2026-09-12 的生产级修复批次也使用 `P0-01`、`P0-02`、`P0-03`、`P0-05`，**含义完全不同**。
   例：`migrations/versions/020_tenant_composite_fk.py` 的 `P0-05` 指跨租户组合外键；
   `scenarios/hr_case_agent/service.py` 的 `P0-05` 指 ACL 单源。同名不同义，同义不同名。
2. **编号无仓库内定义**。09-12 批次的编号写在代码注释、测试 docstring 与提交信息里，
   但仓库内没有任何文件定义这些编号的含义或出处。

---

## 2. 两套体系的来源

| 体系 | 时间 | 编号范围 | 定义载体 | 是否在仓库内 |
|---|---|---|---|---|
| **SEAL-2026-08-31** | 2026-08-31 | `P0-01`～`P0-05` + `CONN-*`/`KNOW-*`/`TASK-*`/`CULT-*`/`HRCASE-*`/`FE-*`/`API-*` | `docs/upgrade/2026-08-31_SEAL_FIX_LEDGER.md` | ✅ 是 |
| **PROD-2026-09-12** | 2026-09-12 | `P0-01`～`P0-07`、`P1-04`～`P1-08`、`P2-09` | 生产级审计报告（**未入库**） | ❌ 否 |

> **诚实声明**：`PROD-2026-09-12` 体系各条目的「含义」一列，是**从代码引用点与提交信息反推**的，
> 不是原始报告原文。如与原始审计报告有出入，以原始报告为准，并请回填本表。

---

## 3. PROD-2026-09-12 登记表

### P0 组

| 编号 | 含义（反推） | 落地提交 | 代码/测试锚点 |
|---|---|---|---|
| P0-01 | 输入护栏前置于 query 改写之前：不可信输入不得触达任何 LLM；流式路径同样保持该顺序 | `1ae4aa6` | `app/scenarios/policy_qa/orchestrator.py:71,103,253`；`tests/rag/test_policy_qa_guard_order.py:105,150,195` |
| P0-02 | 日志卫生：可观测日志永不落原文，只记长度/哈希/命中规则类 | `1ae4aa6` | `app/guardrails/input_guard.py:57`；`app/scenarios/policy_qa/preprocessors.py:8` |
| P0-03 | 历史裁剪方向修正：按"最新优先"填 token 预算，保证最近一轮不被挤出 | `eca339c` | `tests/policy_qa_context_manager_test.py:118` |
| P0-05 | ACL 单源：HR 案件审批人收窄为 `hr_manager`，与服务层、路由、前端对齐 | `d4b7cad` | `app/scenarios/hr_case_agent/service.py:65`；`tests/hr_case/test_case_service.py:132` |
| P0-07 | 生产基线冻结：把验证面绑定 SHA + 锁文件哈希 + 时间戳，产物自校验、拒绝脏树冻结 | `796faaa` / `22ebefc` | `scripts/freeze_production_baseline.py:5`；`docs/production-baseline.json` |

> `P0-04`、`P0-06` 在仓库内无任何引用点，未登记。

### P1 组

| 编号 | 含义（反推） | 落地提交 | 代码/测试锚点 |
|---|---|---|---|
| P1-04 | 检索证据块从 `system` role 降级为 `user` role 的不可信数据块，并加边界标记；文档文本不得携带系统指令权限 | `751787a` | `app/scenarios/policy_qa/context_manager.py:228` |
| P1-05 | LLM 模型名不可跨 provider 移植：引入 per-provider 模型映射与请求级降级 | `e6fd0b1` | `app/rag/llm/fallback.py:41`；`app/rag/llm/model_router.py:56`；`app/rag/llm/orchestrator.py:345`；`app/scenarios/policy_qa/orchestrator.py:49`；`tests/rag/test_model_router.py:225,304` |
| P1-08 | 毒性词分级：整体替换型 hard-block 与放行加标记型 review-flag 分开，避免误禁 HR 核心业务词 | `0f4c785` | `tests/rag/test_guardrails_regressions.py:58` |

> `P1-01`～`P1-03`、`P1-06`、`P1-07` 在仓库内无任何引用点，未登记。

### P2 组

| 编号 | 含义（反推） | 落地提交 | 代码/测试锚点 |
|---|---|---|---|
| P2-09 | 确定性 chunk id（`uuid5`）+ 未变 chunk 向量复用：同文档同位次重建得同一 id，PG/Milvus upsert 幂等；sha 未变的 chunk 不重嵌入；版本清理只删 obsolete id | `e8e0822` | `app/rag/ingestion/pipeline.py:49` |

> `P2-09` 原先只在提交信息中出现，代码锚点写作 `(P2)`，已在本表登记时补齐为 `(P2-09)`。

---

## 4. 重号冲突清单

| 编号 | SEAL-2026-08-31 含义 | PROD-2026-09-12 含义 |
|---|---|---|
| `P0-01` | OAuth callback/revoke/pause 竞态 | 输入护栏前置于改写之前 |
| `P0-02` | HRCase 外部副作用 crash consistency | 日志不落原文 |
| `P0-03` | approval decision 原子性 | 历史裁剪方向修正 |
| `P0-05` | tenant composite integrity（组合外键） | ACL 审批人单源 |

`P0-04` 仅属 SEAL 体系（knowledge decision 原子性）；`P0-07`、`P1-*`、`P2-*` 仅属 PROD 体系。

---

## 5. 消歧约定

自本表起，**引用编号必须带体系前缀**，禁止裸写：

- ✅ `SEAL-P0-05` / `PROD-P0-05`
- ❌ `P0-05`（无法判断指哪一条）

已合入历史提交的裸编号**不追溯改写**（改写会破坏提交与代码的对应关系）；
按 §4 对照表消歧即可。新增批次请在本表追加一节，并在引用处使用带前缀写法。

---

## 6. 待办

- [ ] 回填 `PROD-2026-09-12` 各条目的原始报告原文（若报告可入库）
- [ ] 补齐 `P1-01`～`P1-03`、`P1-06`、`P1-07`、`P0-04`、`P0-06` 是否存在的结论（当前无引用点，需确认是未落地还是未引用）
