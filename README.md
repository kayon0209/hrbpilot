# HRBP AI 工作台 · HRBP AI Workbench

面向 HRBP（人力资源业务伙伴）的企业级 AI 平台，覆盖 **5 大 HR 场景**，提供统一的知识库管理、评测反馈与多租户治理。后端 FastAPI + 异步全链路，前端 React/Vite，向量检索基于 Milvus，异步任务基于 Celery。

> 本仓库包含工程代码与产品文档。**二进制知识库模板（`backend/kb_docs/`）与含个人信息的样本面谈记录（`backend/interview_samples/`）因体积与隐私原因不纳入版本库**，请按 `.env.example` 自行准备。

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-61DAFB.svg)](https://react.dev)
[![License](https://img.shields.io/badge/License-Proprietary-lightgrey.svg)](LICENSE)

## ✨ 五大核心场景

| 场景 | 路由 | 能力 |
|------|------|------|
| 制度问答 Policy QA | `/policy-qa` | 基于知识库的自然语言制度解读，附引用与依据 |
| 面谈话术洞察 Interview Digest | `/interview` | 将面谈录音 / 纪要提炼为结构化洞察与跟进建议 |
| 员工声音分析 Voice Insight | `/voice` | 聚合舆情 / 调研文本，输出情绪与主题洞察 |
| 周报生成 Weekly Report | `/weekly` | 按模板自动生成结构化周报 |
| 文化内容 Culture Content | `/culture` | 生成符合企业文化的宣传 / 活动文案 |

每个场景以 **配置驱动**：新增场景只需提供 `config.yaml` + `prompts/*.txt`，无需改动主干（详见 `backend/app/scenarios/`）。

## 🧰 平台能力

- **知识库管理（KB）**：文档上传、解析、向量化、索引与版本管理
- **评测与反馈（Eval）**：答案质量评测、人工反馈回收、持续改进闭环
- **设置中心（Settings）**：运行时切换 LLM Provider / 模型，无需重启
- **企业级中间件**：多租户隔离 + 认证（JWT）+ RBAC 角色权限 + 结构化日志

## 🏗️ 技术架构

```
React/Vite 前端
   │  (JWT Auth, SSE)
   ▼
FastAPI 网关（多租户 + Auth + RBAC）
   ├─ Scenario Routers（5 场景，配置驱动）
   ├─ KB / Eval / Settings 平台服务
   ├─ RAG Pipeline：Retriever → Milvus → LLM Orchestrator
   └─ Celery 异步任务队列
        │
PostgreSQL · Redis · MinIO · Milvus
```

## 🧰 技术栈

| 分类 | 技术 |
|------|------|
| 后端 | FastAPI, Pydantic v2, SQLAlchemy(async), Alembic |
| 前端 | React 18, Vite, TypeScript, TailwindCSS, Radix UI, Zustand |
| 检索 | Milvus 向量库, Sentence-Transformers |
| 任务 | Celery + Redis |
| 存储 | PostgreSQL, Redis, MinIO(对象存储) |
| 认证 | JWT (python-jose), RBAC |
| 部署 | Docker / docker-compose（含 nginx 反代） |

## 🚀 快速开始（Docker）

```bash
# 1. 准备配置
cp .env.example .env
# 编辑 .env：填入 DATABASE_URL / REDIS_URL / JWT_SECRET / LLM_* 等

# 2. 启动全套依赖与服务
docker compose up --build

# 3. 访问
# 前端:        http://localhost:3000
# 后端 API 文档: http://localhost:8000/docs
```

本地开发（不含向量库）详见 `backend/` 与 `frontend/` 内的说明。

## 📁 项目结构

```
hrbp-ai-workbench/
├── backend/
│   ├── app/
│   │   ├── access/        # 路由：auth / kb / eval / settings + 5 场景
│   │   ├── scenarios/     # 5 大场景（config.yaml + prompts）
│   │   ├── rag/           # 检索与 LLM 编排
│   │   └── main.py        # 应用入口
│   ├── tests/
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/
│   ├── src/               # React 源码
│   └── Dockerfile
├── docker-compose.yml
├── .env.example
├── PRD.md                 # 产品需求文档
└── README.md
```

## 📄 文档

- [产品需求文档 PRD](./PRD.md)

## 🔒 安全与合规

- `.env` 已被忽略，密钥**不**入库
- `backend/kb_docs/`（二进制模板）与 `backend/interview_samples/`（含个人姓名）已排除
- 许可证：**Proprietary**（如需开源授权请另行联系作者）

## 🔗 仓库

https://github.com/kayon0209/hrbp-ai-workbench
