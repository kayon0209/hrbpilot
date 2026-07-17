# 产品需求文档（PRD）｜HRBP AI 工作台（HRBP AI Workbench）

> 文档版本：v1.0 ｜ 创建：2026-07-17 ｜ 状态：基于代码逆向整理（项目此前无产品文档）
> 代码基线：hrbp-ai-workbench 0.1.0
> 保密级别：内部文件

---

## 1. 产品摘要

HRBP AI 工作台是面向 HRBP（HR 业务伙伴）的**企业级统一 AI 平台**，覆盖 5 大 HR 业务场景，将分散的 HR 文档问答、访谈分析、员工声音洞察、周报生成、文化内容创作整合到一个受控、可审计的工作台中。

核心解决 HRBP 的痛点：
- 制度问答依赖人工，口径不一致
- 面试/访谈记录人工整理耗时，风险信号易遗漏
- 员工声音（问卷/反馈）缺乏系统聚类与趋势追踪
- 周报撰写重复劳动，多源数据难汇总
- 文化内容多渠道发布风格不统一

---

## 2. 五大业务场景

### 2.1 政策问答（Policy QA）— `policy_qa`
- **功能**：基于制度知识库的 RAG 问答，回答员工/HR 关于报销、考勤、晋升等政策问题
- **Prompt 约束**：只据文档片段回答、每条必带引用、无依据明确说明、引用格式 `【引用: {文档名} §{章节号}】`
- **检索**：hybrid 策略 + Top-5 + Cross-Encoder 重排
- **护栏**：输入 PII 检测 + Prompt 注入防护；输出引用校验 + 事实性检查
- **角色**：employee（普通员工可用）

### 2.2 面试纪要（Interview Digest）— `interview_digest`
- **功能**：从面试录音转写文本中结构化抽取
- **产出（JSON）**：
  - `employee_demands`：核心诉求（工作环境/薪酬福利/职业发展/团队关系/管理制度 + 紧急度）
  - `risk_level`：HIGH / MEDIUM / LOW
  - `risk_signals`：离职倾向、情绪异常、团队冲突、制度不满
  - `action_items`：可落地行动项（含负责人建议、截止时间）
  - `suggested_owner` + `summary`（200 字内）
- **降级**：数据不足时明确告知"数据不足以做完整分析"

### 2.3 语音洞察（Voice Insight）— `voice_insight`
- **功能**：对员工声音数据（访谈/问卷）聚类分析 + 风险识别 + 趋势判断
- **产出（JSON）**：
  - `clusters`：相似诉求归群（每群 3-8 条，2-4 字标签）
  - `risk_signals`：风险描述 + 严重度 + 来源回溯（source_ids）+ 趋势（上升/稳定/下降）
  - `trends`：主题趋势方向 + 置信度 + 证据
  - `summary`（200 字）
- **特点**：强调原文回溯路径，可审计

### 2.4 周报生成（Weekly Report）— `weekly_report`
- **功能**：汇总多源数据生成结构化 HRBP 周报
- **数据来源**：访谈结果、声音洞察报告、制度问答热点、系统事件
- **产出（JSON）**：
  - `period`：报告周期
  - `progress`：进展条目（带数据来源 + 状态）
  - `risks`：风险预警（严重度 + 跟进人 + 应对措施）
  - `plan`：下周计划（优先级 + 截止日期）
  - `data_sources`：缺失数据明确标注
- **约束**：整体摘要 ≤ 300 字

### 2.5 文化内容（Culture Content）— `culture_content`
- **功能**：基于关键词 + 文化素材，一次生成 4 个渠道适配内容
- **渠道规格**：
  | 渠道 | 字数 | 风格 |
  |------|------|------|
  | 新闻稿 news_article | 800-1200 | 正式庄重 |
  | 群通知 group_notice | 100-200 | 简洁有力 |
  | 员工故事 employee_story | 500-800 | 温情叙事 |
  | 活动文案 event_copy | 200-400 | 吸引号召 |
- **约束**：每渠道风格不同、正面积极、具体不空泛、不像 AI 生成

---

## 3. 平台能力（跨场景）

### 3.1 知识库管理（KB）— `kb`
- 创建/列出/获取知识库
- 文档上传（upload）+ 触发入库（ingest）
- 文档列表 + 删除（单文档/整库）
- 每场景绑定独立 `knowledge_base_id`（如 policy_kb）

### 3.2 评测与反馈（Eval）— `eval` / `feedback`
- 场景级指标查询（citation_accuracy / answer_relevance 等）
- 指标趋势（trend）
- Golden 数据集管理（按场景）
- 用户反馈提交 + 反馈指标聚合

### 3.3 设置（Settings）— `settings`
- LLM Provider 列表查询
- **运行时切换 Provider**（智谱 GLM-4 / DeepSeek V3 / OpenAI GPT-4o）
- Provider 连通性测试

### 3.4 安全与多租户
- 中间件链：RequestID → CORS → TenantContext → Auth(JWT) → RBAC → Handler
- 多租户隔离（TenantContext）
- RBAC 角色权限（创作动作 vs 审批动作分离）
- 安全响应头（SecurityHeaders）
- 结构化日志（structlog）

---

## 4. 用户画像

| 用户 | 使用场景 |
|------|---------|
| HRBP（核心） | 全部 5 场景，日常工作台 |
| 招聘官 | 面试纪要 |
| 文化/品牌 | 文化内容 |
| 员工 | 政策问答（受限角色） |
| 管理者 | 周报、语音洞察趋势 |

---

## 5. 功能范围（MoSCoW）

### Must Have
- 5 大场景可交互（政策问答/面试纪要/语音洞察/周报/文化内容）
- 知识库管理（上传/入库/删除）
- 多 Provider 接入与运行时切换
- 引用强制 + 事实性检查（政策问答）
- 评测指标 + Golden 数据集
- 多租户 + Auth + RBAC

### Should Have
- 用户反馈闭环
- 指标趋势可视化
- 风险信号告警

### Could Have（v2+）
- 场景间数据联动（周报自动汇总洞察）
- 工作流编排（定时周报）

### Won't Have（本期）
- 公开 ToC 产品
- 模型 Fine-tune

---

## 6. 系统架构

```
前端（Vite + React + Tailwind）
        │  HTTPS / JSON
        ▼
FastAPI 中间件链
RequestID→CORS→Tenant→Auth→RBAC→Handler
        │
        ├─ 场景路由（5 场景 Orchestrator）
        │     ├─ RAG Pipeline：Ingestion→KnowledgeBase→Retrieval(hybrid+RRF+rerank)→LLM
        │     └─ 场景 Prompt（Jinja2 模板，含输出 JSON Schema 约束）
        ├─ 知识库（KB CRUD）
        ├─ 评测（Metrics + Golden + Feedback）
        └─ 设置（LLM Provider 切换）
        │
LLM Orchestrator（多 Provider）
  智谱 GLM-4（主）→ DeepSeek V3（备）→ OpenAI GPT-4o（兜底）
        │
存储：PostgreSQL(asyncpg) + Milvus(向量) + Redis(缓存/Celery) + MinIO(对象)
```

---

## 7. 技术栈

| 组件 | 技术 |
|------|------|
| 后端 | FastAPI（Async）+ SQLAlchemy Async + Alembic |
| 向量库 | Milvus（pymilvus） |
| 关系库 | PostgreSQL + asyncpg |
| 缓存/队列 | Redis + Celery |
| 对象存储 | MinIO |
| 聚类 | HDBSCAN |
| Embedding | sentence-transformers（bge 系列） |
| LLM | 智谱 GLM-4 / DeepSeek V3 / OpenAI GPT-4o（OpenAI-compatible） |
| 前端 | Vite + React + TailwindCSS |
| 日志 | structlog（结构化） |
| 工程 | ruff + mypy + pytest |

---

## 8. 场景配置规范（Scenario Config）

每个场景由 `config.yaml` 声明，包含：

```yaml
scenario_id: policy_qa
knowledge_base_id: policy_kb
retrieval_strategy: hybrid      # hybrid / dense / sparse
retrieval_top_k: 5
rerank_enabled: true
prompt_template: prompts/policy_qa.txt
output_schema: QAResponse        # 输出 JSON Schema 约束
guardrail_rules:
  input:  [pii_detection, prompt_injection]
  output: [citation_verification, factuality_check]
eval_metrics: [citation_accuracy, answer_relevance]
fallback_strategy: no_evidence
max_tokens: 1024
temperature: 0.1
required_role: employee
```

> 设计亮点：场景与模型解耦，新增场景只需新增 `config.yaml` + `prompts/*.txt` + `schemas.py`，无需改主干。

---

## 9. 安全与合规

- **Prompt 注入防护**：输入层检测越狱指令
- **PII 检测**：输入层识别敏感信息
- **引用校验 + 事实性检查**：输出层防止编造
- **无证据降级**：`no_evidence` 策略返回"未在现有制度中找到相关依据"
- **多租户隔离 + RBAC**：服务端重鉴权，前端状态非安全边界
- **低温度生成**（temperature 0.1）：政策类回答稳定性优先

---

## 10. 验收标准 Checklist

- [ ] 5 场景均可返回结构化 JSON 结果
- [ ] 政策问答引用准确、无依据时明确拒答
- [ ] 知识库上传→入库→检索链路通
- [ ] Provider 运行时切换 + 降级正常
- [ ] 评测指标可查 + Golden 数据集可用
- [ ] 多租户 + Auth + RBAC 生效
- [ ] 结构化日志可追溯

---

## 11. 已知限制

- 当前为 0.1.0 早期版本，前端为基础壳
- 情感/聚类依赖 LLM + HDBSCAN，极端分布可能不稳定
- 无独立 holdout 评测报告（需补评测基线）

---

## 12. 下一步

1. 补齐各场景 Golden 数据集与评测基线
2. 前端工作台完善（场景导航 + 结果可视化）
3. 场景间数据联动（周报自动聚合洞察/访谈）
4. 风险信号告警推送

---

*本文档基于代码逆向整理（main.py 路由、scenarios/*/prompts、config.yaml、app 模块结构、pyproject.toml），反映 0.1.0 实际能力。此前项目无任何产品文档，本文件为首发。*
