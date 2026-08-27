# Policy QA 引用质量错误分析摘要（2026-08-27）

> 模式：**OFFLINE-DETERMINISTIC**（确定性检索层评测，合成制度库，无 LLM / 无网络）
> 数据：`evaluation/results/policy_qa_error_analysis_2026-08-27.json`
> 规格依据：`docs/evaluation/CITATION_METRIC_SPEC.md`
> **不可用于对外宣称**——本测量只覆盖「检索→结构化引用」层，非 REAL-LLM 端到端。

## 结果 vs 门禁

| 指标 | 门禁 | 实测 | 判定 |
| --- | --- | --- | --- |
| source_recall | ≥ 0.80 | **0.9333** | ✅ |
| source_precision | ≥ 0.85 | **1.0000** | ✅ |
| 无答案识别（拒答样本 5 条全走护栏/无证据路径） | ≥ 0.90 | 5/5 正确进入 no_expectation | ✅（检索层证据） |
| 结构化 Citation 字段完整率 | = 1.00 | **1.0000** | ✅ |

样本构成：50 条（45 条可评分 + 5 条注入拒答不参与引用评分）。

## 错误分类分布

| 类别 | 数量 | 说明 |
| --- | --- | --- |
| full_match | 43 | 期望来源全部被检索并正确绑定引用 |
| partial_match | 1 | 试用期工资打几折（见下） |
| retrieval_miss | 1 | 年假顺延（见下） |
| binding_loss / version_or_alias_mismatch | 0 | 生产引用绑定修复后未再出现 |
| no_expectation | 5 | 注入拒答样本 |

## 生产/评测路径对齐修复（已提交 `8b05689`）

1. **citations 镜像已用证据**：无证据降级（`no_evidence_fallback`）触发时不再返回 LLM 幻觉引用，`citations=[]` 与降级答案一致；`has_evidence` 语义同步收紧。
2. **文档名归一化**：生产 chunk `source` 是文件名（`员工手册.pdf`），golden 是制度名（`员工手册`）——指标层统一剥离扩展名/书名号/全半角后匹配。

## 检索层真实失败类（corpus 迭代中发现并修复）

- **章节粒度过粗**：合成「员工手册」把年假/病假/事假/丧假/哺乳假压进一个 section，长 chunk 在词重叠排名上压制专题文档（与真实 PG `ts_rank_cd` 行为一致）。拆分后专题文档恢复竞争力。
- **词汇错位**：文档正文用「多少天」而 golden 提问用「几天」——jieba 分词后无交集。这映射真实生产问题：**入库文档与员工问法的词汇差异**，Phase 3+ 的 query rewrite 层应覆盖。

## 剩余 2 条 = Golden 标签质量存疑（按红线未改标签）

1. `年假没休完能顺延到明年吗？` → 期望 `薪酬福利管理制度`；但该事实在 `休假管理制度`（已被检索并引用，rank #2）。标签指向了错误文档类型。
2. `试用期工资打几折？` → 期望 `劳动合同`+`薪酬制度`；`薪酬制度`（解析到薪酬福利管理制度）已引用 rank #1；`劳动合同管理规定` 的试用期条款在 chunk 级竞争中稳定排 top-5 之外——两标签之一存疑。

**建议**：这两条标签交由人工复核（属 `candidate` 修正流程），不由评测侧擅自改写。

## 下一步（进 Phase 3 前）

- 本门禁通过的是检索→引用层；端到端 REAL-LLM 测量需先解决宿主 5432 端口被本地 PostgreSQL 服务抢占的问题（容器库连不上），以及补齐 `EMBEDDING_API_KEY`。
- `claim_support_rate` / `citation_binding_accuracy` 需 Citation Binder 增强（当前生产 binder 未接入 pipeline），列入后续 Phase。
