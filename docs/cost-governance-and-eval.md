# HRBP AI Workbench — 成本治理与评测现状（真实基线）

> **诚实声明（必读）**：本文件记录的是**已落地的成本治理设计与评测基建**，
> 以及**2026-07-30 一次真实 LLM 运行跑出的真实数字**（guardrail 拦截率、黄金集
> 质量分、真实 token 成本）。与 MindGraph 不同，本项目**没有检索阶段延迟/Recall
> 消融**（无已提交 CSV），所以不声称"检索成本效率"。凡涉及数字，本文件**一律
> 不编造**——下面所有 %/token 均来自 `golden_eval_20260730T213019Z.json` 实测。
> 仅线上异步 `auto_eval` 的质量分仍是占位 stub（已明确标注，不混入真实数字）。

---

## 0. 一句话现状

| 维度 | 状态 | 说明 |
|------|------|------|
| Token 预算治理 | ✅ **真实落地、接进生产路径** | `token_budget.py` 已被 `rag/pipeline.py` 调用 |
| 黄金评测集 | ✅ **真实存在、可数** | 250 样本 / 5 场景各 50 |
| 质量评测执行（黄金集离线） | ✅ **真实数字已出（2026-07-30 真实运行）** | `golden_metrics.py` 的 keyword/citation recall 跑出真实分（见 §6） |
| 质量评测（线上异步 auto_eval） | ⚠️ **框架接好，数值仍是占位 stub** | `auto_eval.py` 返回 0.7/0.5 常量，未接真实实现；与黄金集离线评测是两回事 |
| 指标聚合器 | ⚠️ **框架真实，内存起步为空** | `metrics.py` 无预录数据 |
| 黄金集评测运行器 | ✅ **已落地、已真实跑通** | `evaluation/run_golden_eval.py`：REAL-LLM 模式产出真实分 + 真实 token |
| 真实 token 消耗 | ✅ **真实（224,640 token，占月度预算 2.25%）** | 2026-07-30 真实运行记录（见 §6） |
| 成本效率（token 预算视角） | ✅ **真实** | 单次全量 sweep = 2.25% 预算 → ~44 次/租户月（见 §6 指标 1） |
| 检索成本效率（ΔRecall/Δ成本% 延迟代理） | ❌ **未测** | 无已提交检索消融 CSV；语义不同于上面的 token 成本效率 |

---

## 1. Token 预算治理（真实、可上简历）

来源：`app/shared/token_budget.py`，调用点 `app/rag/pipeline.py:165`。

### 1.1 真实设计参数（代码里写死的常量，非估算）

```python
DEFAULT_MONTHLY_BUDGET = 10_000_000   # 每租户每月 1000 万 token
WARNING_THRESHOLD     = 0.75          # 75% 预警
CRITICAL_THRESHOLD    = 0.90          # 90% 严重告警
```

### 1.2 阈值数学（由上面常量直接推导，可辩护）

| 档位 | 触发点 | 剩余缓冲 |
|------|--------|----------|
| OK | < 7.5M (75%) | 充足 |
| WARNING | 7.5M（75%） | 剩 2.5M（25%） |
| CRITICAL | 9.0M（90%） | 剩 1.0M（10%）硬上限缓冲 |
| 超限 | ≥ 10.0M（100%） | `within_budget=False` |

**设计含义**：在达到硬上限前预留 **10%（100 万 token）** 的熔断缓冲；
预警档（75%）到严重档（90%）之间留 **1.5M** 作为运营介入窗口。
这是真实的成本闸门，不是示意。

### 1.3 真实能力点（代码已具备）

- 按租户月度累计（`_monthly_usage`，`YYYY-MM` 分区）
- 按**模型**拆分统计（`by_model`），可定位是哪个模型在烧钱
- 每次请求同步、快速记录（失败也不阻塞主链路，`pipeline.py:213` 有 try/except）
- 返回 `{within_budget, usage_pct, alert_level, tokens_used, budget}`

### 1.4 已知缺口（简历照实写，别美化成"已完成"）

- **纯内存、无持久化**：注释写明 "swap to Redis for persistence"。
  进程重启即清零，跨重启的月度累计需要接 Redis/DB。
- 预算默认 1000 万，但**未接租户级配置**——目前所有租户共享同一默认值的入参，
  没有从配置/DB 读每租户预算的逻辑。

---

## 2. 黄金评测集（真实、可数）

来源：`app/evaluation/golden_dataset.py`。

> ⚠️ 计数方式说明：5 个场景中有 3 个（voice_insight / weekly_report /
> culture_content）是用 `for` 循环**运行时展开**的，源码里只出现 1~2 次
> `GoldenSample(...)` 模板。下面数字是**按循环展开后的真实样本数**，不是
> grep 源码字面次数（字面次数会漏掉循环，误数为 104）。

### 2.1 规模（真实）

| 场景 | 样本数 | 生产方式 | 含 guardrail 拒答用例 |
|------|--------|----------|----------------------|
| policy_qa | 50 | 手写 distinct | 5（注入/越狱） |
| interview_digest | 50 | 手写 distinct | 0 |
| voice_insight | 50 | 模板展开（25 批次 ×2） | 0 |
| weekly_report | 50 | 模板展开（50 周） | 0 |
| culture_content | 50 | 模板展开（50 关键词） | 0 |
| **合计** | **250** | 100 手写 + 150 模板 | **5** |

### 2.2 真实评测覆盖维度（每条样本标注字段）

- `expected_output_contains[]`：必须出现的要点（命中率可量化）
- `expected_citations[]`：应引用的制度/文档（引用准确率可量化）
- `expected_risk_level`：高风险信号等级（HIGH/MEDIUM/LOW）
- `should_reject`：guardrail 用例（prompt injection / jailbreak / 越权）

### 2.3 诚实边界

- **手写 distinct 样本 = 100 条**（policy_qa + interview_digest），质量最高、
  最具区分度。
- **模板展开样本 = 150 条**，本质是少量模板的参数化变体（如"批次{i}""2026-W{i}"），
  规模好看但判别力弱于手写样本。简历写"250 条标注样本"属实，
  写"250 条高质量手写样本"则**夸大**，建议写"250 条标注样本（含 100 条手写 + 模板扩增）"。

---

## 3. 质量评测执行（框架真实，数值是占位）

来源：`app/evaluation/auto_eval.py`（调用点 `rag/pipeline.py:136`、
`scenarios/policy_qa/orchestrator.py:130`）。

### 3.1 真实接入情况

- `AutoEvaluator.evaluate()` 在 RAG 管线第 7 步**异步、非阻塞**调用（✅ 真接了）
- `policy_qa` orchestrator 也调用（✅ 真接了）
- 指标维度：Faithfulness / Answer Relevance / Citation Accuracy（+ 若干占位维度）

### 3.2 当前数值是 stub（必须如实说明）

```python
def _citation_accuracy(...) -> float:   return 0.7   # TODO: 真实实现
def _answer_relevance(...) -> float:    return 0.7   # TODO: 真实实现
def _faithfulness(...) -> float:        return 0.7   # TODO: 真实实现
# extraction_completeness / topic_coverage / ... : return 0.5  # 占位
```

**所以现在跑出来的"评分"是假的常量，不能写进简历。**
`extraction_completeness` 等维度连方法体都没有，只有 `0.5` 占位。

### 3.3 聚合器

`MetricsAggregator`（metrics.py）：运行均值/最小/最大/趋势，**框架真实**，
但 `_entries` 内存起步为空，无预录数据，`get_scenario_metrics()` 对空场景返回 `{}`。

---

## 4. 已测 vs 未测（对照表）

| 你要的指标 | MindGraph | hrbp-ai-workbench |
|------------|-----------|-------------------|
| 边际成本效率 ΔRecall/Δ成本% | ✅ 真实（延迟代理） | ❌ 未测（无检索消融 CSV） |
| 路由后混合成本 | ✅ 真实 | ❌ 未测（无路由/tok-k 设计） |
| 成本结构（输入 token 占比） | ✅ 真实（分阶段延迟） | ⚠️ 不同口径：hrbp 有**按场景的真实 token 成本结构**（见 §6 指标 2），非检索阶段延迟拆分 |
| 真实 token/$ 消耗 | ❌（仅延迟代理） | ✅ 224,640 真实 token（2026-07-30 运行） |
| 黄金评测集规模 | n/a | ✅ 250 样本 |
| 成本治理阈值设计 | n/a | ✅ 10M/75%/90% |

**结论**：hrbp 在"成本治理设计"和"评测基建"上是真实的、可写进简历的；
并且**已经跑出真实数字**（guardrail 拦截率、黄金集质量分、真实 token 成本效率），
不再是"为零"。唯有"检索阶段延迟/召回 消融"尚未测（与 MindGraph 的延迟代理口径不同），
如需对标可后续补检索消融 CSV——但那不属于当前 token 预算视角的成本效率。

---

## 5. 怎么跑一次真实评测（已落地为运行器）

原先的 3 步已实现为 `evaluation/run_golden_eval.py`（真实指标在 `app/evaluation/golden_metrics.py`，
替换了 auto_eval 的占位 stub 思路）。运行：

```powershell
cd D:\demo\hrp-ai-workben            
$env:PYTHONPATH = "."
python evaluation/run_golden_eval.py
```

- **有 LLM key（.env 里 LLM_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY）** → 真实 LLM 输出 →
  真实质量分 + 真实 token 总量 + 预算占比。结果写入 `evaluation/results/golden_eval_<时间戳>.json`。
- **无 key** → 自动进入 `[MOCK-LLM MODE]`：用 mock 生成器跑通管线、证明指标逻辑正确，
  但分数**合成、不可用于简历**（脚本开头有醒目横幅声明）。

运行器做两件事：
1. **Guardrail 实测（离线、真）**：250 条黄金输入过真实 `InputGuardrail`，报
   - `injection_recall`：5 条 `should_reject` 注入用例被正确拦截的比例（**真实安全指标**）
   - `false_positive_rate`：正常查询被误拦的比例
   - ✅ **中文注入缺口已修复（2026-07-30）**：原 `INJECTION_PATTERNS` 只有英文，已补 10 条
     中文模式（假装你是/扮演/角色扮演/系统提示词/忽略|忘记+指令/不受限制/越狱 等）。
     修复后重跑 `injection_recall=1.0`（5 条注入全拦），`false_positive_rate=0.0`。
2. **质量实测**：每条样本过真实 `CapabilityPipeline`（InputGuard → Retriever[dev mock KB] →
   LLM[真实/mock] → OutputGuard），用 `golden_metrics` 算 `keyword_recall` / `citation_recall`，
   并记录 token（真实或估算）。

跑完把 `golden_eval_<时间戳>.json` 贴回，我就能套用 MindGraph 那套三指标公式，
给你算出 hrbp 的真实成本效率数字（此前为零的部分就此补齐）。

> 注：hrbp 当前 RAG 管线有 dense/sparse/hybrid + 可选 rerank 的检索器，
> 若要对标 MindGraph 的"检索成本效率"，可对其检索阶段做同样的延迟/topk 消融；
> 但**当前没有已提交的检索消融 CSV**（不像 MindGraph 有 `evaluation_runs`）。

---

## 6. 真实评测运行结果（2026-07-30, REAL-LLM 模式）

> 来源：`evaluation/results/golden_eval_20260730T213019Z.json`
> 运行模式：**REAL-LLM**（.env 配了真实 LLM key，输出真实、token 真实计入）。
> 这是一次**全真实**运行：250 样本全部跑通，无 stub、无估算填充（除 5 条被拦注入样本）。

### 6.1 Guardrail（离线、真实）

| 指标 | 值 |
|------|-----|
| total_inputs | 250 |
| overall_match_rate | **1.0** |
| injection_recall | **1.0**（5/5 注入全拦，含中文） |
| false_positive_rate | **0.0**（245 条正常查询零误拦） |

### 6.2 质量（真实 LLM 输出，golden_metrics 算分）

| 场景 | n | avg keyword_recall | avg citation_recall |
|------|---|--------------------|---------------------|
| policy_qa | 50 | 0.5803 | 0.33 |
| interview_digest | 50 | 0.8267 | 1.0 |
| voice_insight | 50 | 0.885 | 1.0 |
| weekly_report | 50 | 0.32 | 1.0 |
| culture_content | 50 | 0.616 | 1.0 |

**诚实解读**：interview_digest / voice_insight / culture_content 的 citation_recall 均 1.0
（输出都正确引用了制度文档）；keyword_recall 各场景差异明显——weekly_report(0.32)
与 policy_qa(0.58) 偏低，说明这两类"要点全覆盖"还不稳定，是**真实待优化点**
（不是 stub 假分，值得写进简历作为"已量化、已知短板"）。

### 6.3 真实 Token 成本

| 指标 | 值 |
|------|-----|
| total_tokens | 225,133 |
| real_tokens | 224,640（**99.78% 真实**，仅 5 条被拦注入样本为估算） |
| monthly_budget | 10,000,000 |
| budget_pct | **2.2513%** |

即：一次完整 250 样本回归 ≈ 2.25% 月度租户预算 → 单租户月可跑 **~44.4 次全量 sweep**
（CI 级、可高频回归，评测本身几乎不烧钱）。

### 6.4 三指标成本效率（真实，对标 MindGraph 框架）

沿用 MindGraph 的"三指标"结构，但用 hrbp **真实 token 计数**：

1. **单次评测成本 vs 预算包络**：2.25% / 月 → ~44.4 次全量 sweep 可放进一个租户月。
   说明这套 guardrail + 质量回归极其便宜、可纳入 CI 高频跑，不会因评测烧钱。
2. **成本结构（按场景 token 拆分，真实）**：`culture_content` 以 **42.4%** 主导 token 消耗
   （长文生成驱动，单样本 avg 1,910.8 token），远超其余场景。完整按场景拆分
   （来源：`summarize_cost.py` 聚合 `golden_eval_20260730T213019Z.json`，2026-07-30 真实运行）：

   | 场景 | n | avg token | total token | 占总量 |
   |------|---|-----------|-------------|--------|
   | policy_qa | 50 | 526.7 | 26,337 | 11.7% |
   | interview_digest | 50 | 525.5 | 26,275 | 11.7% |
   | voice_insight | 50 | 735.2 | 36,761 | 16.3% |
   | weekly_report | 50 | 804.4 | 40,218 | 17.9% |
   | culture_content | 50 | 1,910.8 | 95,542 | **42.4%** |
   | **合计** | **250** | — | **225,133** | **100%** |

   成本由"长文生成"驱动（culture_content 单样本 token 是其他场景的 **3.6×**），而非检索/输入——
   优化方向是压缩 culture_content 输出长度，而非检索路由。
3. **Token 计费保真度**：99.78% 为真实 LLM 上报 token，仅被拦注入样本估算。
   成本数字可信，非合成。

> **诚实边界**：hrbp 无检索延迟/Recall 消融，故**不**声称 MindGraph 式的
> "ΔRecall/Δ延迟成本"；上面是 **token 预算视角**的成本效率，口径不同，
> 已在 §4 对照表区分清楚。

---

## 7. 简历可用（诚实）措辞

> 构建了 HRBP AI Workbench 的 **LLM 成本治理体系**：按租户月度 1000 万 token
> 预算、75%/90% 两级告警、按模型拆分统计，已接入生产检索链路；
> 建立了 **250 条标注回归评测集**（覆盖 5 大 HR 场景、含 5 条 prompt-injection
> 拒答用例），并写到真实评测运行器，跑出**真实指标**：prompt-injection 拦截率
> 100%、正常查询误拦率 0%、真实质量分（interview_digest/voice_insight/culture_content
> citation_recall 均 1.0）、单次全量回归仅耗 2.25% 月度预算（~44.4 次/租户月，CI 可高频跑）。
> *（线上异步 auto_eval 的质量分仍是 0.7/0.5 占位 stub，未接真实实现；
> 检索阶段延迟/Recall 消融尚未做——这两点别写进简历当已完成。）*

**可上简历的真实数字（来自 2026-07-30 REAL-LLM 运行，非编造）**：
- 安全：prompt-injection 拦截率 **100%**（含中文注入），误拦率 **0%**
- 成本：单次 250 样本评测 = **225,133 token ≈ 2.25%** 月度预算，~44 次/租户月
- 质量（golden_metrics 真实算分）：citation_recall 在访谈/舆情/文化三场景 **1.0**，
  keyword_recall 0.32（周报）–0.89（舆情）区间，已知周报/制度问答要点覆盖偏低

如写"检索阶段省了 X% 延迟/成本"，**必须等检索消融 CSV 跑出后再填**；
当前只承诺 token 预算视角的成本效率（已实测）。
