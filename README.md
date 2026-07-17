# HRBP AI Workbench

> 面向 HRBP 的企业级 AI 平台 —— 制度问答、面谈洞察、员工声音、周报生成、文化内容创作

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=white)](https://react.dev)
[![Milvus](https://img.shields.io/badge/Milvus-Vector_DB-1A73E8?logo=vector-database&logoColor=white)](https://milvus.io)
[![License](https://img.shields.io/badge/License-Proprietary-lightgrey)](./LICENSE)

## 核心场景

| 场景 | 能力 | 输出 |
|------|------|------|
| **制度问答** Policy QA | 基于企业知识库的自然语言制度解读 | 结构化答案 + 引用来源 + 依据条款 |
| **面谈话术洞察** Interview Digest | 将面谈录音 / 会议纪要提炼为结构化分析 | 洞察要点 + 跟进建议 + 风险标记 |
| **员工声音分析** Voice Insight | 聚合舆情 / 调研文本进行情绪与主题分析 | 情绪分布 + 主题聚类 + 关键词提取 |
| **周报自动生成** Weekly Report | 按企业模板从零散输入生成结构化周报 | 完整周报正文 + 数据摘要 + 下周计划 |
| **文化内容创作** Culture Content | 生成符合企业文化调性的宣传 / 活动文案 | 多版本文案 + 渠道适配建议 |

每个场景以 **配置驱动**：新增业务场景只需提供 `config.yaml` + prompt 模板，无需改动主干代码。

## 架构

```
┌─────────────────────────────────────────┐
│         React/Vite 前端 (TypeScript)      │
│    JWT Auth · SSE 实时更新 · Radix UI     │
└──────────────┬──────────────────────────┘
               │
┌──────────────▼──────────────────────────┐
│       FastAPI 应用网关                     │
│  ┌─────────────────────────────────┐    │
│  │  Scenario Routers (配置驱动)      │    │
│  │  5 大场景 × config.yaml + prompts│    │
│  ├─────────────────────────────────┤    │
│  │  平台服务                          │    │
│  │  KB 管理 · 评测反馈 · 设置中心      │    │
│  ├─────────────────────────────────┤    │
│  │  RAG Pipeline                    │    │
│  │  Retriever → Milvus → LLM        │    │
│  └──────────────┬──────────────────┘    │
│                 │                        │
│  ┌──────────────▼──────────────────┐    │
│  │  Celery 异步任务队列             │    │
│  └─────────────────────────────────┘    │
└──────────────┬──────────────────────────┘
               │
┌──────────────▼──────────────────────────┐
│  PostgreSQL · Redis · MinIO · Milvus     │
└─────────────────────────────────────────┘
```

## 快速开始

```bash
git clone https://github.com/kayon0209/hrbp-ai-workbench.git
cd hrbp-ai-workbench

# 准备环境变量
cp .env.example .env
# 编辑 .env：填入 DATABASE_URL / REDIS_URL / JWT_SECRET / LLM_API_KEY 等

# Docker 一键启动全套依赖与服务
docker compose up --build

# 访问
#   前端:          http://localhost:3000
#   后端 API 文档: http://localhost:8000/docs
```

本地开发（不含向量库）详见各子项目目录内的说明。

## 项目结构

```
hrbp-ai-workbench/
├── backend/
│   ├── app/
│   │   ├── access/        # 路由层：auth / kb / eval / settings + 5 场景路由
│   │   ├── scenarios/     # 5 大场景实现（每场景 = config.yaml + prompts + handler）
│   │   ├── rag/           # RAG 检索管线 + LLM 编排器
│   │   └── main.py        # FastAPI 入口
│   ├── tests/             # 测试套件
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/              # React/Vite 前端源码
├── docker-compose.yml     # 全栈编排
├── .env.example           # 环境变量模板
├── PRD.md                 # 产品需求文档
└── README.md              # 本文件
```

## 技术选型

| 层 | 技术 |
|---|------|
| 后端框架 | FastAPI + Pydantic v2 + SQLAlchemy (async) + Alembic |
| 前端 | React 18 + Vite + TypeScript + TailwindCSS + Radix UI + Zustand |
| 向量检索 | Milvus + Sentence-Transformers |
| 异步任务 | Celery + Redis Broker |
| 存储 | PostgreSQL + Redis + MinIO (对象存储) |
| 认证授权 | JWT (python-jose) + RBAC 角色权限模型 |
| 部署 | Docker Compose (含 nginx 反代) |

## 文档

- [产品需求文档 PRD](./PRD.md)

## License

Proprietary — All rights reserved.
