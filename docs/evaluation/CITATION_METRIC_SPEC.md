# Citation 指标规格（CITATION_METRIC_SPEC）

> 版本：v1.0（2026-08-27，Phase 1.4 冻结）
> 状态：**定义冻结**。实现切换见 §5 演进计划；切换完成前不得把 legacy 指标对外宣称为本规格指标。

---

## 1. 目的与背景

当前 `app/evaluation/golden_metrics.py::citation_recall` 通过「答案字符串是否包含期望文档名（如『员工手册』）」来近似引用质量。该做法有以下缺陷：

1. **与生产接口脱节**：生产路径 `QAResponse.citations` 是结构化字段，答案子串匹配无法反映其真实内容；
2. **可被刷分**：在答案末尾追加文档名即可提高分数，属于计划明令禁止的作弊路径；
3. **无法区分召回与精度**：文档名出现 ≠ 引用正确。

本规格冻结四个替代指标的定义。**评测必须优先读取生产路径的结构化 `citations`**；若生产管道某场景无法产出结构化 Citation，正确动作是修复生产/评测路径一致性，而不是在评测侧放宽口径或拼凑答案文本。

## 2. 术语与输入约定

- `answer`：管道最终输出文本。
- `citations`：生产路径返回的结构化引用列表，元素至少包含
  `{"doc": str, "section": str | null, "chunk_id": str, "quote": str}`。
  （字段以 `QAResponse.citations` 实际 schema 为准；本规格要求 `doc` 与
  `section`/`chunk_id` 至少其一非空。）
- `expected_sources`：Golden 样本的 `expected_citations`（期望文档名列表）。
- `key_claims`：从 `answer` 中抽取的关键主张集合（抽取方式见 §4.3）。

## 3. 四个指标定义

### 3.1 `source_recall`（来源召回）

```
source_recall = |expected_sources ∩ cited_docs| / |expected_sources|
```

- `cited_docs` = `citations` 中去重后的 `doc` 集合；
- 分子按文档名归一化匹配（去空格、全半角统一、大小写不敏感；不做语义匹配）；
- `expected_sources` 为空时该指标记为 **N/A**（不计入聚合，不记 0）。

### 3.2 `source_precision`（来源精度）

```
source_precision = |cited_docs ∩ supporting_docs| / |cited_docs|
```

- `supporting_docs`：检索证据中实际支持答案的文档集合（由 Citation Binder
  绑定结果或人工标注给出）；
- `citations` 为空而答案含事实性内容时，记 `0.0`（真实 0 分，不是 skip）；
- 该指标衡量「引用的来源中有多少真的支撑答案」，防止堆砌引用刷召回。

### 3.3 `claim_support_rate`（主张支撑率）

```
claim_support_rate = |key_claims supported by evidence| / |key_claims|
```

- 主张是否被 `citations[].quote` 所在 chunk 的内容支持；
- `key_claims` 为空（答案无事实主张，如纯拒答/澄清）时记 **N/A**；
- 与 `source_precision` 的区别：作用于「答案的主张」而非「引用的文档」，
  能检出「引了正确文档但答案编造细节」的情况。

### 3.4 `citation_binding_accuracy`（引用绑定正确率）

```
citation_binding_accuracy = |citations with correct doc+section/quote binding| / |citations|
```

- 判定标准：引用的 `quote` 确实出现在所指 `doc`/`chunk_id` 对应内容中，
  且 `doc` 归一化匹配；
- `citations` 为空记 **N/A**（没有绑定可判，不记 0——与 §3.2 的语义不同）；
- 该指标衡量「引用片段和文档/章节绑定是否正确」，防止 chunk_id 张冠李戴。

### 3.5 N/A 与 0 的语义（重要）

| 情形 | 语义 |
| --- | --- |
| 期望来源为空 / 无主张 / 无引用可判 | `N/A`，不计入分母 |
| 有期望来源但引用缺失、引用错误 | 真实 `0.0`，计入 |
| LLM judge / 评测组件故障 | **skip**（沿用 Phase 1.2 的 `skipped_metrics` 语义，绝不产生 0 或 N/A） |

## 4. 测量规则

### 4.1 数据来源

- 一律读取生产路径的结构化 `citations`；评测脚本禁止解析答案文本来构造引用集合（legacy 实现除外，见 §5）。
- 多来源问题必须保留全部结构化引用参与计算，**不得只取 Top-1**。

### 4.2 归一化

- 文档名匹配前做：strip、全角→半角、大小写折叠、去书名号/引号；
- 禁止「包含即匹配」以外的模糊匹配（如编辑距离、语义相似度）。

### 4.3 主张抽取

- 首版允许确定性规则抽取（按句号/分号切句、过滤无谓语短句），并在结果中记录抽取器版本；
- 引入 LLM 抽取时，其失败按 §3.5 的 skip 语义处理。

### 4.4 模式标注

- 任何引用指标的测量结果必须标注运行模式：`REAL-LLM` / `MOCK-LLM` / `OFFLINE`；
- `MOCK-LLM` 模式下的引用指标不可用于对外宣称（沿用 runner 的
  `for_external_claims` 与 `mock_notice` 机制）。

## 5. 演进计划与 legacy 处置

| 阶段 | 动作 |
| --- | --- |
| 现在（Phase 1.4） | 冻结本规格；`golden_metrics.citation_recall` 继续存在但在结果中标注 `legacy_substring` |
| Phase 2 | 打通生产 `QAResponse.citations` → 评测路径；实现 `source_recall` / `source_precision` 并进 Policy QA 错误分析；`claim_support_rate` / `citation_binding_accuracy` 随 Citation Binder 增强落地 |
| legacy 下线 | 四指标全部有实测数据且口径核对一致后，删除 `legacy_substring` 指标，README 更新指标口径说明 |

## 6. 反作弊约束（红线）

1. 禁止向答案文本追加文档名以提升任何引用指标；
2. 禁止为迁就当前模型输出修改 `expected_citations` 标签；
3. 禁止把 `legacy_substring` 分数当作本规格四指标对外报告；
4. 禁止在 `citations` 缺失时用检索 top-k 文档名顶替引用参与打分。
