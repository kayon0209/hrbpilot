# HRBPilot 角色 × 能力矩阵

> 由 `scripts/render_capability_matrix.py` 从 `app/access/middleware/rbac.py` 直接渲染。
> 改了 RBAC 不重跑本脚本，文档就会与实现不一致 —— 请勿手改本表。

## 1. 角色 × 能力

| 能力 | 说明 | 员工 | HRBP | HR 经理 | 平台管理员 |
| --- | --- | :-: | :-: | :-: | :-: |
| `audit_read` | 审计查询 | — | — | — | ✅ |
| `culture_content` | 文化内容 | — | ✅ | ✅ | — |
| `data_source_admin` | 数据源管理 | — | — | — | ✅ |
| `employee_request` | 我的请求 | ✅ | — | — | — |
| `evaluation` | 评测 | — | — | — | ✅ |
| `hr_case` | HR 案例 | — | ✅ | ✅ | — |
| `hr_request_triage` | 请求分诊 | — | ✅ | ✅ | — |
| `interview_digest` | 面试纪要 | — | ✅ | ✅ | — |
| `kb_management` | 知识库管理 | — | — | — | ✅ |
| `knowledge_feedback` | 知识反馈 | — | — | ✅ | — |
| `notifications` | 通知 | ✅ | ✅ | ✅ | — |
| `policy_qa` | 制度问答 | ✅ | ✅ | ✅ | — |
| `settings` | 系统设置 | — | — | — | ✅ |
| `user_admin` | 用户管理 | — | — | — | ✅ |
| `voice_insight` | 声音洞察 | — | ✅ | ✅ | — |
| `weekly_report` | 周报 | — | ✅ | ✅ | — |
| `work_summary` | 工作总结 | — | ✅ | ✅ | — |

## 2. 路由 → 所需能力 → 可访问角色

| 路由前缀 | 所需能力 | 可访问角色 |
| --- | --- | --- |
| `/api/kb` | `kb_management` | 平台管理员 |
| `/api/eval` | `evaluation` | 平台管理员 |
| `/api/audit` | `audit_read` | 平台管理员 |
| `/api/settings` | `settings` | 平台管理员 |
| `/api/policy-qa` | `policy_qa` | 员工、HRBP、HR 经理 |
| `/api/my-requests` | `employee_request` | 员工 |
| `/api/hr-requests` | `hr_request_triage` | HRBP、HR 经理 |
| `/api/v1/hr-cases` | `hr_case` | HRBP、HR 经理 |
| `/api/admin/users` | `user_admin` | 平台管理员 |
| `/api/data-sources` | `data_source_admin` | 平台管理员 |
| `/api/notifications` | `notifications` | 员工、HRBP、HR 经理 |
| `/api/voice-insight` | `voice_insight` | HRBP、HR 经理 |
| `/api/weekly-report` | `weekly_report` | HRBP、HR 经理 |
| `/api/work-summaries` | `work_summary` | HRBP、HR 经理 |
| `/api/culture-content` | `culture_content` | HRBP、HR 经理 |
| `/api/interview-digest` | `interview_digest` | HRBP、HR 经理 |
| `/api/knowledge-feedback` | `knowledge_feedback` | HR 经理 |

## 3. 设计说明（面试可讲的三条）

1. **角色之间不继承**：能力是集合，不是等级。平台管理员 `admin` **默认拿不到任何 HR 业务内容**（面试/声音/周报/案例都不行）——需要业务能力必须显式持有业务角色，避免「管理员看到全公司员工数据」。
2. **三层鉴权**：路由前缀由 `RBACMiddleware` 粗粒度拦截 → 对象级 ACL 在服务层（`access/object_scope.py`）→ `tenant_id` 只作隔离边界、不参与授权判定。
3. **不在路由表里的路径没有中间件保护**：例如 `/api/material-batches` 依赖 handler 内的能力门，这是唯一一道门 —— 新增路由时必须显式登记。

---

*自动生成，勿手改。*