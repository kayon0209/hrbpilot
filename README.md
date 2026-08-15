# HRBPilot · 企业 HR 领域智能体

> 用一套后端覆盖 5 大高频 HR 场景，内置 RAG 检索、合规护栏、评测与 token 预算管控——让 AI 在 HR 场景里**用得起、防得住**。

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![Pydantic](https://img.shields.io/badge/Pydantic-v2-9c2bd1.svg)](https://docs.pydantic.dev)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)

## 为什么是 HRBPilot，而不是「又一个 HR 问答 Bot」

HR 场景的痛点是**风险与成本**，不是"能不能答出来"。一句不合规的答复、一次超预算的调用，都比"答得不够聪明"代价更高。HRBPilot 把护栏（guardrails）和成本治理（token budget）做成**一等公民**：输入先过护栏、输出再过护栏、每次调用都计入租户月度预算。

## 核心能力

- **5 大 HR 场景**（各自独立的 orchestrator + config + prompts + schemas）
  - `policy_qa` 制度问答：基于知识库的政策咨询，含预处理 / 后处理
  - `interview_digest` 面谈纪要：绩效 / 离职 / 入职面谈记录的智能摘要
  - `culture_content` 文化内容：企业文化相关内容生成
  - `voice_insight` 语音洞察：语音 / 会议内容的洞察提炼
  - `weekly_report` 周报：自动周报生成
- **RAG 检索**：`dense` / `sparse` / `hybrid` 三策略，可选 rerank；生产对接 Milvus / Qdrant，dev 模式内置 mock 知识库可端到端联调
- **合规护栏（guardrails）**：输入护栏、输出护栏、合规校验、限流（rate limiter）
- **评测（evaluation）**：按场景聚合并持久化质量指标（avg / min / max / 趋势），含 golden dataset 接口
- **Token 预算管控**：按租户月度 token 预算（默认 1000 万）统计消耗，75% 预警 / 90% 严重告警
- **多租户 + 安全**：Auth / RBAC / 租户上下文中间件，结构化日志（structlog）

## 系统架构

![HRBPilot 系统架构](./assets/architecture.svg)

```
Client
   │
   ▼
API 网关（Auth / RBAC / 租户上下文中间件）
   │
   ▼
5 × Scenario Orchestrator（policy_qa / interview_digest / culture_content / voice_insight / weekly_report）
   ├─ RAG Pipeline（dense / sparse / hybrid + 可选 rerank）
   ├─ Guardrails（输入护栏 → 合规校验 → 输出护栏 → 限流）
   ├─ Token Budget（租户月度预算 · 75% 预警 / 90% 严重）
   └─ Evaluation（质量指标聚合 + golden dataset）
   │
   ▼
存储层：PostgreSQL(Alembic) · Redis · Celery · MinIO · Milvus
```

## 评测（真实、REAL-LLM 跑通）

2026-07-30 在 **250 样本 golden 集**（5 场景各 50，含 5 条注入拒答用例）上以真实 LLM 跑通：

| 维度 | 结果 |
|------|------|
| 护栏 overall | **1.0** |
| 注入拦截 recall（injection_recall） | **1.0**（5/5 全拦，已补中文正则） |
| 误拦率（false_positive） | **0.0** |
| Token 总消耗 | 225,133（99.78% 真实计费） |
| 预算占用 | 占 10M 月度预算 **2.25%** → 约 **44 次 / 租户 / 月** |

各场景质量（golden_metrics，citation 覆盖率）：

| 场景 | 关键词命中 | 引用覆盖率 |
|------|-----------|-----------|
| policy_qa | 0.58 | 0.33 |
| interview_digest | 0.83 | 1.0 |
| voice_insight | 0.89 | 1.0 |
| weekly_report | 0.32 | 1.0 |
| culture_content | 0.62 | 1.0 |

> 完整结果见 `app/evaluation/` 与 `evaluation/results/` 下对应运行产物。

## 技术栈

FastAPI · Uvicorn · Pydantic v2 · SQLAlchemy(async) + AsyncPG · Alembic · Redis · Celery · MinIO · Milvus · Sentence-Transformers · OpenAI / Anthropic SDK · PyYAML

## 目录结构

```
hrbp-ai-workbench/
├── app/
│   ├── access/        # 中间件链 + 路由（health/auth/各场景/eval/kb/settings）
│   ├── config/        # 配置（pydantic-settings）
│   ├── data/          # 数据库 / models / repositories / alembic migrations
│   ├── evaluation/    # 评测指标聚合 + golden dataset
│   ├── guardrails/    # 合规 / 输入 / 输出护栏 + 限流
│   ├── rag/           # ingestion / knowledge_base / llm / retrieval / pipeline
│   ├── scenarios/     # 5 个 HR 场景（各自 orchestrator+config+prompts+schemas）
│   └── shared/        # 日志 / 错误 / 审计 / 缓存 / token_budget 等
├── interview_samples/ # 面谈记录样本（.txt）
├── kb_docs/           # HR 知识库原始文档（PDF / DOC / DOCX）
├── tests/             # pytest
└── alembic.ini · Dockerfile · pyproject.toml · .env.example
```

## 快速开始

```bash
python -m venv .venv && source .venv/Scripts/activate   # Windows
pip install -e ".[dev]"
cp .env.example .env            # 按需填写
uvicorn app.main:app --reload --port 8000
# API 文档: http://localhost:8000/docs
```

> dev 模式未连接 Milvus 时，检索器返回内置 mock 知识库，可完整跑通链路。

## 测试

```bash
pytest
```

## 已知限制（诚实边界）

- **Token 预算为纯内存实现**，无持久化——重启后计数清零，生产化需接数据库。
- **线上异步 `auto_eval` 的质量分（Faithfulness / Relevance / Citation）目前仍是占位 stub（返回 0.7）**，未接真实评测模型；本 README 的 golden 指标来自离线 `golden_eval` 脚本的真实运行。
- `weekly_report` 场景的关键词命中（0.32）偏低，主要因周报自由文本难用关键词衡量，已在优化中。

## License

[MIT](./LICENSE) —— 详见 [LICENSE](./LICENSE) 文件。
