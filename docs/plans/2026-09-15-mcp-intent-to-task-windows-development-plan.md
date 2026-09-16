# HRBPilot MCP Intent-to-Task：Windows 开发执行主文档

- 日期：2026-09-15
- 适用分支基线：`codex/external-assistant-mcp`
- 当前核对提交：`9d4375d feat(mcp): 完善外部 Agent 授权与实时撤销`
- 执行平台：Windows 11 + Docker Desktop（WSL2 后端）+ PowerShell 7
- 文档状态：**待 Windows 本地开发与验收；未获用户确认前禁止 push**
- 配套文档：
  - 调研证据：`docs/plans/2026-09-14-github-product-hunt-mcp-research.md`
  - 分歧裁定：`docs/plans/2026-09-14-mcp-plan-adjudication-and-merge.md`
  - P0 操作细节：`docs/ops/2026-09-15-windows-mcp-p0-playbook.md`

---

## 1. 先给结论：这次到底要做什么

目标不是在 HRBPilot 里再造一个聊天机器人，而是把 HRBPilot 建成外部 AI Agent 可安全调用的 **Intent-to-Task Gateway（意图到任务网关）**：

```text
HR / HR 经理在 Codex、WorkBuddy 等 Agent 中输入自然语言
        ↓ Agent 负责理解语言并选择少量任务型 MCP 工具
HRBPilot 校验身份、角色、scope、客户端安装实例
        ↓
prepare_hr_action 生成服务端冻结的任务草稿
        ↓ 缺信息则补问；信息完整则展示人类可读确认摘要
用户明确确认（确认的是冻结草稿，不是 Agent 临时拼出的参数）
        ↓
submit_hr_action 进入现有审批 / outbox / worker 执行链
        ↓
get_task_status / list_my_tasks 返回唯一可信状态与下一步动作
```

用户看到的体验应是：**授权后能验证是否真的可用；说一句话后能看懂系统准备做什么；写操作不会偷偷执行；离开对话后仍能找到任务并继续处理。**

### 1.1 首版范围

1. 修通授权后的三层断点：scope 套餐、客户端工具确认、任务受理层；
2. 对普通 HR 暴露 5–7 个任务型工具，保留现有 11 个原子工具给高级/兼容端点；
3. 实现服务端冻结草稿、缺字段补充、显式确认、审批、异步执行、状态轮询和取消；
4. 把 `/mcp` 改成“AI 助手接入与诊断”，把 `/tasks` 升级为含“AI 发起任务”的任务中心；
5. 形成可量化的路由、安全、确认疲劳、结果预算与可用性门禁；
6. 完成 Codex 与 WorkBuddy 的真实客户端验收和第一批 HR 培训材料。

### 1.2 首版明确不做

- 不把项目内聊天窗口作为主入口；仅在外部客户端覆盖不足时再做演示/兜底；
- 不在首版下沉低层 SDK 接 MCP Tasks；先用普通 MCP tools + HTTP/OAuth + 轮询任务工具；
- 不使用已废弃的 `sampling`、`roots`、`logging` 设计服务端反向调用模型；
- 不把 `work_tasks`、`approval_requests`、`async_tasks` 强行合并成一张表；
- 不让外部 Agent 持有或转发第三方系统密钥；HRBPilot token 只访问 HRBPilot；
- 不为尚未出现的集中式 IdP 需求提前建设 Enterprise Managed Authorization（EMA）。

---

## 2. 已验证事实与设计约束

### 2.1 真实客户端验收事实

| 实测项 | 结果 | 产品含义 |
| --- | --- | --- |
| Codex 输入“告诉我连接身份能使用哪些能力” | 自动选中 `get_my_access_profile` | 自然语言到工具选择已成立，无需自建意图识别模型 |
| Codex 工具审批策略为 `never` | 客户端侧拒绝调用 | OAuth 授权不等于客户端允许每次工具调用，诊断页必须分层说明 |
| 客户端放行后 | 返回 `hrbp/default` | 身份透传链路成立 |
| 当前 token scope | 仅 `hrb:profile:read` | 11 个工具中只有一个可用，授权套餐必须修复 |
| WorkBuddy | 能识别项目级 MCP，但显示 `Needs authentication` | 发现服务不等于完成认证；需要独立登录与 smoke test |

因此，“连接成功”必须拆成三层状态：

1. **认证状态**：是否拿到有效 OAuth token；
2. **能力状态**：角色能力 ∩ token scope ∩ 客户端上限后还剩哪些工具；
3. **调用状态**：客户端自身是否允许调用、HRBPilot 是否能受理并追踪任务。

### 2.2 协议基线

- 当前项目依赖 `mcp >= 2.2.0`，协议世代为 `2026-07-28`；
- MCP Tasks 已是稳定扩展，但项目使用的高层 `MCPServer` 未暴露 `tasks`、`discover`、`elicitation`；接入需要下沉低层接口，因此不是首版前置条件；
- `sampling`、`roots`、`logging` 已进入废弃路径，不新增依赖；
- 首版的最大兼容面是：Streamable HTTP + OAuth + tools + 应用层任务状态轮询。

### 2.3 现有可复用资产

| 能力 | 现有位置 | 本次用法 |
| --- | --- | --- |
| MCP 服务与原子工具 | `app/mcp/server.py` | 保留高级端点，并在其上增加任务型 facade |
| 三重权限交集 | `app/mcp/auth/authorization.py` | 所有 prepare、submit、status 调用继续复用 |
| 审批与参数哈希 | `ApprovalRequest.input_hash` 等 | 冻结草稿和提交时再次校验 |
| 工作任务幂等 | `work_tasks.idempotency_key` | 任务落地时复用，防双击和重试重复创建 |
| OAuth、DCR、安装实例撤销 | `app/oauth/`、`app/access/routes/admin_mcp.py` | 连接与任务均绑定 client / installation |
| 任务页面 | `web/src/features/tasks/TasksPage.tsx` | 增加“AI 发起”分区，不复制整套工作任务 UI |
| MCP 页面 | `web/src/features/mcp/McpPage.tsx` | 改为连接、诊断、权限说明和轻量工具体验 |
| 设计系统 | `web/src/styles/tokens.css`、`DESIGN.md` | 不引入新视觉框架，复用令牌与组件 |
| WorkBuddy Skill | `connectors/workbuddy/skills/hrbpilot/SKILL.md` | 从单一 capability manifest 校验/生成内容 |

---

## 3. 统一工期与发布门禁

工期按一个熟悉仓库的开发者估算，不再使用互相冲突的版本：

| 阶段 | 目标 | 预计工作日 | 进入条件 | 退出条件 |
| --- | --- | ---: | --- | --- |
| P0 | 接入链路收官 | 3–4 | Compose 可启动 | Codex/WorkBuddy 三项 smoke test 通过 |
| P1 | Intent-to-Task 后端 MVP | 6–8 | P0 通过 | 冻结草稿→确认→审批→状态闭环通过 |
| P2 | 任务中心与连接诊断 UI/UX | 4–5 | API 契约冻结 | 桌面/移动/键盘/错误态验收通过 |
| P3 | 评测、治理、培训与发布准备 | 3–4 | P1/P2 通过 | 指标达标、文档齐全、用户验收通过 |

所有阶段完成后仍停在本地。只有用户明确回复“Windows 本地验收通过，可以 push”，才进入 §13。

---

## 4. Windows 环境与依赖

### 4.1 必备软件

| 软件 | 要求 | 验证命令 |
| --- | --- | --- |
| Windows | Windows 11，已启用虚拟化 | `winver` |
| WSL2 | Docker Desktop 使用 WSL2 backend | `wsl --status` |
| Docker Desktop | 已启动，Compose v2 可用 | `docker version`; `docker compose version` |
| Git for Windows | 支持长路径，附带 OpenSSL | `git --version`; `openssl version` |
| PowerShell | 推荐 7.x | `$PSVersionTable.PSVersion` |
| Python | 3.12.x，安装时勾选加入 PATH | `py -3.12 --version` |
| Node.js | 22.x，附带 Corepack | `node --version`; `corepack --version` |
| Codex CLI | 能在同一台机器唤起浏览器 OAuth | `codex --version` |
| WorkBuddy | Windows 桌面版、已登录 | 在应用内确认版本 |
| 浏览器 | Edge 或 Chrome | 可访问 `http://localhost:3001` |
| 可用磁盘 | 至少 20 GB | `Get-PSDrive C` |

Docker 用于运行完整服务依赖；Python 3.12 与 Node 22 用于本地开发、静态检查和测试。不要用系统 Python 3.13/3.14 代替项目声明的 3.12。

### 4.2 Windows 特有准备

在 PowerShell 中执行：

```powershell
New-Item -ItemType Directory -Force C:\work | Out-Null
Set-Location C:\work
git config --global core.autocrlf input
git config --global core.longpaths true
```

仓库放在纯 ASCII、无空格路径下，例如 `C:\work\HRBPilot`。后续 HTTP 探针使用 `curl.exe`，避免 PowerShell 把 `curl` 解释为别名。

### 4.3 克隆与安全分支

```powershell
Set-Location C:\work
git clone https://github.com/kayon0209/hrbpilot.git HRBPilot
Set-Location C:\work\HRBPilot
git fetch origin
git switch codex/external-assistant-mcp
git pull --ff-only
git log -1 --oneline
git status --short
git switch -c codex/mcp-intent-to-task-v1
```

预期：基线提交为 `9d4375d` 或其明确后继；工作区为空；开发发生在 `codex/mcp-intent-to-task-v1`，不直接改远端基线分支。

若 `git status --short` 有内容，先停止并保存输出，不要 reset、checkout 覆盖或删除。

### 4.4 环境变量与固定 OAuth 签名密钥

```powershell
Copy-Item env.docker.example env.docker
openssl ecparam -genkey -name prime256v1 -noout -out C:\work\hrbpilot-oauth-signing.pem
openssl base64 -A -in C:\work\hrbpilot-oauth-signing.pem -out C:\work\hrbpilot-oauth-signing.b64
Get-Content C:\work\hrbpilot-oauth-signing.b64
```

用 VS Code 或记事本编辑 `env.docker`：

- `APP_ENV=development`；
- 设置不少于 32 字符的 `JWT_SECRET`；
- 填入真实的 LLM、embedding、MinIO 配置；
- 把上一步单行内容填入 `OAUTH_SIGNING_KEY_PEM`；
- 不提交 `env.docker`、`.pem`、`.b64` 或任何真实密钥。

### 4.5 首次启动

先安装开发依赖：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
corepack enable
corepack pnpm --dir web install --frozen-lockfile
```

预期：Python 依赖安装完成，`web\node_modules` 由 pnpm 创建，锁文件没有非预期变化。若 `git status --short` 显示 `pnpm-lock.yaml` 被改动，先查 Node/pnpm 版本，不要直接提交。

再启动完整服务：

```powershell
docker compose up -d --build
docker compose ps
curl.exe -sS -o NUL -w "app health %{http_code}`n" http://localhost:8001/api/health
curl.exe -sS -o NUL -w "app ready  %{http_code}`n" http://localhost:8001/api/ready
curl.exe -sS -o NUL -w "oauth ready %{http_code}`n" http://localhost:8002/ready
curl.exe -sS -o NUL -w "web        %{http_code}`n" http://localhost:3001
```

预期：`postgres`、`redis`、`app`、`oauth-as` healthy；四个探针均返回 200。

常见问题见 §12。首次迁移可能耗时 1–3 分钟，不要在 `app` 尚未 healthy 时把它判断为代码回归。

---

## 5. P0：先修“授权了仍不能用”

### 5.1 P0-1：记录真实 CLI 能力，不猜参数

```powershell
codex mcp add --help
codex mcp login --help
codex mcp get hrbpilot-local
```

把输出保存到本次 Windows 执行记录。只有当前 Codex CLI 明确支持 scope 参数时，脚本才能传该参数；否则走授权服务器的可配置默认 scope 路线。

预期：明确分辨“CLI 能显式申请 scope”还是“AS 需要在 scope 缺省时提供套餐”。

### 5.2 P0-2：实现授权套餐

固定两个套餐，不允许客户端任意扩权：

| 套餐 | scopes | 默认用户 |
| --- | --- | --- |
| 仅查询 | `hrb:profile:read hrb:policy:read hrb:case:read hrb:approval:read` | HR、HR 经理 |
| 查询 + 提交办理建议 | 查询版 + `hrb:case:propose` | 用户显式选择且注册上限允许 |

实现要求：

1. CLI 支持则由 `scripts/connect-codex-local-mcp.ps1` 显式请求；
2. CLI 不支持则在 OAuth authorize 流程增加可配置缺省值，默认仍为最小权限；
3. 未绑定租户的 DCR 客户端不能自动获得写 scope；
4. 最终判定始终是角色 capability ∩ token scope ∩ 客户端注册上限；
5. 授权页把每个 scope 翻译成业务语言，写 scope 单独强调“只允许提交审批，不代表立即执行”。

应增加/更新测试：OAuth 缺省 scope、显式 scope、超注册上限、跨租户、普通角色请求写 scope、撤销后再次调用。

### 5.3 P0-3：新增 Windows 连接脚本

新增 `scripts/connect-codex-local-mcp.ps1`，职责仅包含：

1. 检查 `codex` 和 `/api/ready`；
2. 让用户选择“仅查询”或“查询 + 提交建议”；
3. 添加或复用 MCP server；
4. 在同一台 Windows 上启动 OAuth；
5. 登录后执行三项 smoke test；
6. 明确输出失败层级，不输出 token 和密钥。

不要让脚本自动修改 Codex 的工具审批策略。客户端侧 `never`、`on-request` 等策略属于用户安全选择，脚本只检测并给出处理建议。

### 5.4 P0-4：把 smoke test 产品化

连接向导结束后必须依次显示：

| 检查 | 调用 | 成功标准 | 失败提示 |
| --- | --- | --- | --- |
| 身份 | `get_my_access_profile` | 返回正确 tenant/role | “已连接，但身份读取失败” |
| 权限 | 比较 effective scopes 与所选套餐 | 完全匹配或更小且解释原因 | “认证成功，但权限套餐不完整” |
| 业务 | `search_policy` 使用安全测试词 | 工具可调用，允许 `NO_EVIDENCE` | “权限可读，但业务工具未能调用” |

`NO_EVIDENCE` 是业务成功但没有资料，不应画成连接失败；401、403、客户端拒绝、服务未就绪必须分别显示。

### 5.5 P0-5：修正现有 UI 与工具契约

必须修正：

- 删除 `McpPage.tsx` / `toolCopy.ts` 中已移除的 `hrbpilot_ping`；
- 删除“查询类允许匿名试用”，改为“所有业务工具均需登录；只读工具不会改数据”；
- 工具定义补齐 annotations、明确 output schema、错误码、是否只读/破坏性/幂等；
- 返回业务名称，不把 UUID 当主要信息；
- `connectors/workbuddy/mcp.json` 的占位 URL 与图标只能在确定发布环境后替换，开发环境不得伪装成正式 URL；
- 保留已经成熟的 `connector-meta.json`，不要从零重写。

### 5.6 P0 完成标准

- Codex 和 WorkBuddy 各完成一次身份、scope、业务工具 smoke test；
- 查询套餐至少能用 4 项对应能力，办理套餐能提交建议但不能绕过审批；
- 客户端侧拒绝时 UI/脚本准确指出是“AI 客户端未放行”，不是“OAuth 失败”；
- 撤销安装实例后下一次调用失败，已有未执行审批也按既有策略失效；
- MCP 页面无过期工具和匿名调用误导文案。

---

## 6. P1：Intent-to-Task 后端 MVP

### 6.1 单一 capability manifest

新增一份机器可读的能力清单，例如 `connectors/hrbpilot-capabilities.json`，包含：

- 任务类型、用户可见名称、示例说法；
- MCP tool 名、输入/输出 schema、风险级别；
- required scopes、支持角色、是否需要审批；
- 缺字段的追问顺序；
- concise/detailed 结果预算；
- Codex 与 WorkBuddy 展示文本来源。

增加校验脚本，确保 MCP 注册、Web 工具文案、WorkBuddy Skill 与 manifest 不漂移。权限判定仍以服务端代码为权威，manifest 不能成为绕过授权的第二套规则。

### 6.2 应用层任务投影，而不是合并内部表

建议新增：

- `app/data/models/agent_task.py`
- 下一条 Alembic migration（实施时先运行 `alembic heads`，不要硬编码版本号冲突）
- `app/scenarios/agent_tasks/service.py`
- `app/access/routes/agent_tasks.py`

`agent_tasks` 建议字段：

| 字段 | 作用 |
| --- | --- |
| `id`, `tenant_id` | 租户内唯一任务 |
| `requester_user_id` | 发起人 |
| `client_id`, `installation_id` | 来源客户端与授权实例 |
| `source_surface` | `codex` / `workbuddy` / `web` / `other` |
| `task_type` | 稳定任务类型，不直接暴露底层函数名 |
| `goal_summary` | 脱敏后的业务目标摘要；默认不保存完整对话 |
| `canonical_params_json` | 服务端规范化后的最小必要参数 |
| `input_hash` | task_type + canonical params 的 SHA-256 |
| `draft_version` | 防止旧确认覆盖新草稿 |
| `status` | 统一状态机 |
| `risk_level`, `risk_reasons_json` | 确认与升级依据 |
| `missing_fields_json`, `planned_steps_json` | 补问和确认卡片 |
| `approval_id`, `work_task_id`, `async_task_id` | 对现有内部对象的引用投影 |
| `expires_at`, `cancelled_at`, timestamps | 生命周期 |

`agent_task_events` 只存安全事件摘要和状态变化，不存 token、密钥、完整提示词或大段员工敏感原文。

### 6.3 状态机

```text
draft
  ├─→ input_required ─→ ready_for_confirmation
  └──────────────────→ ready_for_confirmation
ready_for_confirmation
  ├─→ awaiting_approval ─→ queued ─→ running ─→ succeeded
  │                                      └────→ failed
  ├─→ cancelled
  └─→ expired
```

约束：

- 只允许服务端定义的相邻迁移；
- 每次迁移写 event，携带 request/trace id；
- submit 使用行锁或乐观版本检查，重复提交返回同一任务，不创建重复审批；
- `cancelled`、`expired`、`succeeded`、`failed` 为终态；
- 撤销 installation 后禁止继续 submit 或补充信息；
- 已进入不可撤销外部副作用时，cancel 返回“无法取消，已执行到哪一步”，不能伪装成功。

### 6.4 普通 HR 的任务型 MCP 工具

首版公开 7 个主工具；可选的 input/cancel 可作为第 8、9 个，但默认工具包仍保持 5–7 个高频工具：

| 工具 | 用途 | 副作用 |
| --- | --- | --- |
| `get_my_access_profile` | 身份、角色、有效权限与限制 | 无 |
| `answer_policy_question` | 制度问题 + 引用来源 | 无 |
| `get_case_context` | 获取有权限案件的安全摘要 | 无 |
| `prepare_hr_action` | 编译意图、补信息、生成冻结草稿 | 仅创建草稿 |
| `submit_hr_action` | 用户确认后提交审批/执行 | 创建受治理写流程 |
| `get_task_status` | 单任务当前状态、下一步、错误 | 无 |
| `list_my_tasks` | 当前用户任务列表 | 无 |
| `provide_task_input` | 可选：补充缺失字段 | 更新草稿，无业务写入 |
| `cancel_task` | 可选：取消可取消任务 | 受状态机约束 |

现有 11 个原子工具继续保留在高级 endpoint/bundle，避免破坏兼容性；普通 HR bundle 不默认全部暴露。

### 6.5 `prepare_hr_action` 契约

输入只包含自然语言目标和可选上下文引用，不接受客户端自报 tenant/role：

```json
{
  "goal": "为张三创建试用期异常跟进任务，下周三前由李经理处理",
  "case_ref": "optional-business-reference",
  "response_format": "concise"
}
```

建议输出：

```json
{
  "task_id": "...",
  "draft_version": 1,
  "status": "ready_for_confirmation",
  "interpreted_goal": "为张三建立试用期异常跟进",
  "subject_candidates": [],
  "missing_fields": [],
  "planned_steps": [
    {"label": "创建或关联案件", "effect": "待确认"},
    {"label": "创建跟进任务并指定负责人", "effect": "待审批"}
  ],
  "risk": {"level": "medium", "reasons": ["将创建业务记录"]},
  "required_scopes": ["hrb:case:propose"],
  "expires_at": "...",
  "confirmation_summary": "将提交 2 个步骤供 HR 审批；现在不会直接修改员工数据。"
}
```

服务端在返回前规范化参数、保存冻结草稿并计算 `input_hash`。若用户修改任何字段，创建 `draft_version + 1` 并重新展示确认摘要。

### 6.6 `submit_hr_action` 契约

客户端只能提交：`task_id`、`draft_version`、`idempotency_key`。**不得重新提交业务参数。**

服务端必须：

1. 重新验证用户、tenant、client、installation、scope 与角色；
2. 验证草稿未过期、状态可提交、version 一致；
3. 重算并核对 `input_hash`；
4. 根据风险创建现有 `ApprovalRequest` 或进入安全只读/低风险路径；
5. 绑定 approval、outbox、work task 等引用；
6. 返回任务状态与人类可读下一步。

### 6.7 风险与确认分层

| 风险 | 示例 | 交互 |
| --- | --- | --- |
| 只读 | 查制度、读自己有权看的案件摘要 | 不额外确认 |
| 中风险 | 创建案件、创建任务、调整负责人 | 一次业务摘要确认 + 现有审批 |
| 高风险 | 批量通知、敏感状态变更、跨团队影响 | 强提示、影响范围、必要时二次确认/经理审批 |

治理目标：需要高风险升级的比例控制在 **10%–15%**，并同时监控漏升级。该数字是产品调优目标，不是为达标而降低真实风险等级。

### 6.8 结果预算

所有列表/详情工具统一执行：

- `response_format=concise|detailed`，默认 concise；
- 列表默认 20 条，使用 opaque cursor；
- 单次工具结果上限 25,000 tokens；截断时必须返回 `truncated=true`、`next_cursor` 和下一步提示；
- 主要结果使用人类可读名称，ID 放在辅助字段；
- 默认不返回完整制度文档、完整员工档案或内部调试对象；
- 输出 schema 明确区分 `FOUND`、`NO_EVIDENCE`、`INPUT_REQUIRED`、`AWAITING_CONFIRMATION`、`AWAITING_APPROVAL`、`RUNNING`、`SUCCEEDED`、`FAILED`、`FORBIDDEN`。

### 6.9 P1 测试门禁

新增并通过：

- 状态机所有允许/禁止迁移；
- 同一 idempotency key 并发提交只产生一条审批；
- 旧 draft version 无法提交；
- 客户端修改参数不能绕过 frozen hash；
- 跨租户、越权对象、已撤销安装实例、scope 不足全部拒绝；
- 过期、取消、部分执行失败均返回真实状态；
- 任务列表只能看到发起人或角色对象范围内的任务；
- concise/detailed、分页、截断与敏感字段过滤；
- 既有原子工具回归测试继续通过。

---

## 7. P2：UI/UX 详细执行方案

### 7.1 信息架构

不新增“聊天”导航。调整两处现有入口：

1. `/mcp`：导航名从“MCP 外部工具”改为 **“AI 助手接入”**。面向 HR 显示连接说明、权限、诊断和示例；面向管理员再显示客户端/安装实例治理。
2. `/tasks`：导航名改为 **“任务中心”**。保留现有“我的工作”，新增“AI 发起”视图；HR 经理可多一个“团队待处理”。

### 7.2 AI 助手接入页

桌面结构：

```text
AI 助手接入                                      [运行连接诊断]
让你的 AI 助手安全调用 HRBPilot；写操作先确认、后审批

[连接状态] 已连接 / 需登录 / 权限不完整 / 客户端未放行 / 服务异常
[三步进度] ①连接身份  ②核对权限  ③业务调用测试

[选择助手] Codex | WorkBuddy
[选择权限套餐] 仅查询 | 查询 + 提交办理建议
[接入步骤/复制命令]                [权限解释与数据说明]

[诊断结果]
✓ 身份 hrbp / default
! scope 缺少 case:read             [重新授权]
× 客户端拒绝工具调用               [查看客户端设置]

[可这样说的示例] [工具目录（次级）]
[管理员连接治理，仅 admin]
```

关键文案：

- 不写“授权成功”，写“已认证，正在验证业务权限”；
- 办理套餐按钮写“允许提交办理建议”，不写“允许 AI 修改数据”；
- `NO_EVIDENCE` 写“连接正常，但没有找到匹配资料”；
- 客户端拒绝写“HRBPilot 已授权，但 Codex/WorkBuddy 当前设置不允许这次工具调用”；
- 技术 URL、JSON 与 scope 原文默认折叠，业务解释优先。

### 7.3 任务中心

桌面结构：

```text
任务中心
[我的工作] [AI 发起 3] [团队待处理 2（经理）]

[待我补充] [待我确认] [待审批] [执行中] [已完成] [失败]
[搜索] [来源：全部/Codex/WorkBuddy] [风险] [更新时间]

任务卡片
  试用期异常跟进                          待我确认 · 中风险
  AI 理解：为张三建立跟进，李经理负责，下周三前完成
  来源：Codex  ·  更新于 10 分钟前
  下一步：核对下方计划并提交审批                       [查看并确认]
```

任务详情使用右侧抽屉（窄屏改为独立全屏层）：

- 顶部：业务标题、状态、风险、来源、更新时间；
- “你的原始目标”：默认展示脱敏摘要，不展示完整对话；
- “系统理解”：对象、负责人、截止时间、影响范围；
- “准备执行的步骤”：逐项说明会读取/创建/通知什么；
- “还需要你补充”：只呈现缺失字段，保留已填内容；
- “确认摘要”：确认按钮写 **“提交审批”**，禁止写“立即执行”；
- “处理记录”：时间线显示草稿、补充、确认、审批、执行、失败/取消；
- 失败态：显示用户可采取的动作，技术 request id 放折叠区。

### 7.4 交互状态清单

每个页面/卡片必须设计并测试：

| 状态 | UI 行为 |
| --- | --- |
| loading | 骨架或 `AsyncState`，不跳动布局 |
| empty | 说明为什么为空，并给一个可执行入口 |
| input_required | 聚焦第一个缺失字段；保存后仍可返回修改 |
| ready_for_confirmation | 业务摘要置顶，危险信息不能折叠 |
| submitting | 按钮禁用并显示“正在提交审批…”，幂等 key 保持不变 |
| awaiting_approval | 告知谁来审批、在哪里查看，不伪装执行中 |
| running | 显示当前步骤与最近更新时间；轮询退避 |
| succeeded | 显示真实产出链接和完成时间 |
| failed | 显示可重试/不可重试、用户动作、request id |
| cancelled/expired | 说明未执行内容；提供“复制为新草稿”而非复活旧草稿 |
| offline | 保留当前输入，恢复后手动重试；不静默重复提交 |

### 7.5 视觉与可访问性标准

- 复用 `tokens.css`；不引入 Tailwind、MUI 或另一套颜色体系；
- 正文不小于 16px，业务元信息不小于 13px；状态不能只靠颜色，必须有文字/图标；
- 主按钮每个决策区最多一个；高风险使用 `--danger`，普通警告使用 `--warning`；
- 任务列表不使用无意义分数、徽章、连续打卡等游戏化元素；
- 桌面最大内容宽度沿用 `--content-max`；820px 以下单列；抽屉占满窄屏；
- 所有按钮键盘可达，焦点使用现有 `--focus-ring`；
- dialog 打开后焦点受控，Esc 可关闭，关闭后回到触发按钮；
- 动态状态用 `aria-live="polite"`，错误用 `role="alert"`；
- 支持 `prefers-reduced-motion`；200% 缩放不横向溢出；
- 中文不显示裸 snake_case；时区按浏览器本地时间并标注必要的绝对日期。

### 7.6 前端建议文件边界

```text
web/src/api/agent-tasks.ts
web/src/features/agent-tasks/AgentTaskList.tsx
web/src/features/agent-tasks/AgentTaskCard.tsx
web/src/features/agent-tasks/AgentTaskDrawer.tsx
web/src/features/agent-tasks/AgentTaskConfirm.tsx
web/src/features/agent-tasks/AgentTaskTimeline.tsx
web/src/features/agent-tasks/agentTaskCopy.ts
web/src/features/agent-tasks/AgentTasks.module.css
web/src/features/mcp/ConnectionDiagnostics.tsx
web/src/features/mcp/connectionCopy.ts
```

`TasksPage.tsx` 只负责编排 tabs 和查询，不继续长成单文件巨型组件。复用 `ConfirmDialog`、`StatusBadge`、`AsyncState`，必要时先增强公共组件的 focus restoration 和结构化内容能力。

### 7.7 P2 测试与人工验收

Vitest/Testing Library：

- 角色可见导航；
- tabs、筛选、空态、轮询状态；
- 缺字段补充后版本更新；
- 双击确认只发一次请求；
- 401/403/409/410/422/5xx 对应正确文案；
- dialog 键盘行为和焦点恢复；
- 管理员连接治理不泄露 token；
- 已撤销安装实例任务不能继续提交。

人工浏览器：

- 1440×900、1280×720、390×844 三种尺寸；
- 100% 与 200% 缩放；
- 只用键盘完成连接诊断、补字段、查看确认、取消；
- 开启减少动态效果；
- HRBP、HR 经理、admin 三个角色分别截图；
- 中文长标题、无数据、20 条以上列表、超长失败原因均不破版。

---

## 8. P3：评测、治理、培训与上线准备

### 8.1 自然语言黄金集

建立 50–100 条首版 utterances，至少覆盖：

- 同义说法、口语、省略主语、错别字；
- 一句话包含多步骤；
- 对象歧义与权限外对象；
- 只读问题被误判为写操作；
- 高风险动作、批量动作、撤销动作；
- prompt injection、要求跳过审批、索要密钥；
- 没有证据、没有 case id、没有负责人、相对日期。

指标：

| 指标 | 首版门槛 |
| --- | ---: |
| 任务类型 Top-1 正确率 | ≥ 90% |
| 必要补问正确率 | ≥ 90% |
| 不必要补问率 | ≤ 15% |
| 未授权写操作发生数 | 0 |
| 重试导致重复副作用 | 0 |
| 高风险升级率 | 10%–15%，并人工抽检漏升级 |
| concise 结果遵守分页/预算 | 100% |

### 8.2 数据流向与留存说明

面向 HR 和安全团队提供一页说明：

- AI 能看到什么：仅当前用户授权范围内的最小必要字段；
- HRBPilot 保存什么：任务摘要、规范化参数、审批与状态事件；默认不保存完整外部对话；
- 谁能看：发起人、对象级可见范围内的 HR/经理、必要的管理员审计角色；
- 保存多久：给出明确配置和组织策略，不写模糊的“长期”；
- 如何纠正/删除：任务草稿可取消，已发生的审计不可伪造删除，敏感数据按组织政策处理；
- token 与密钥：不进入任务正文、日志或工具结果。

### 8.3 第一批用户启用

1. 为 HRBP 和 HR 经理分别预置只读/办理 bundle；
2. 每个高频任务给 2–3 条自然语言示例和一个斜杠快捷入口（斜杠命令是辅助，不是新权限边界）；
3. 进行 30 分钟培训：连接、提问、看懂确认、审批、查状态、撤销连接；
4. 指定一名团队 champion 收集失败说法和误路由；
5. 首周每日看失败率与确认升级，不把提示词问题误当用户不会用；
6. Product Hunt 业务产品启示落实为：强制人工检查点、管理员预算/应用权限、业务本体与人类可读实体、透明数据流向。

案例不是照抄功能，而是转化为以下产品原则：

| 业务侧案例 | 借鉴点 | HRBPilot 落地 |
| --- | --- | --- |
| Accordio | 把人工检查点作为合规价值 | 确认卡与审批不是摩擦，而是可解释的保护层 |
| Salins 类斜杠命令体验 | 非技术用户需要可发现的快捷入口 | 高频任务提供快捷命令与自然语言示例，但权限仍由服务端决定 |
| Salestrics | 管理员 token 预算与按应用权限 | 增加客户端 bundle、结果预算和管理员治理视图 |
| Well | 用业务实体本体承载大量能力 | 返回员工、案件、制度等人类可读实体，不把 UUID 当产品语言 |
| Peach | 用户最关心数据流向与留存 | 接入页直接展示“AI 能看什么、保存什么、谁能看、保留多久” |

---

## 9. Windows 上的推荐开发顺序

每个步骤都遵循“先测试 → 小改动 → 聚焦验证 → 记录结果”。

### 步骤 1：冻结基线

```powershell
git status --short
docker compose ps
.\.venv\Scripts\python.exe -m pytest tests/mcp tests/oauth -q
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m mypy app
corepack pnpm --dir web test:run
corepack pnpm --dir web lint
corepack pnpm --dir web build
```

预期：记录 pass/fail/skip 数及失败原文。已有失败不掩盖，但必须区分“基线已有”和“本次新增”。

### 步骤 2：完成 P0

推荐提交边界（仅本地 commit，不 push）：

1. `fix(mcp): align scope onboarding and capability diagnostics`
2. `fix(web): remove stale MCP tools and auth copy`
3. `test(mcp): add client smoke and contract coverage`

每个提交后运行相关 MCP/OAuth/前端测试；完成后执行两客户端真实验收。

### 步骤 3：先定 P1 契约，再建库表

1. 写输出 schema 与状态机测试；
2. 建 migration/model；
3. 实现 service；
4. 接 REST route；
5. 接 MCP facade；
6. 最后接现有 approval/outbox/work task；
7. 并发、幂等、撤销、跨租户测试通过后才进入前端。

迁移验证：

```powershell
docker compose exec app alembic heads
docker compose exec app alembic upgrade head
docker compose restart app worker celery-worker
docker compose ps
```

### 步骤 4：实现 P2 UI

1. 先做 API types 和 mock 测试；
2. 拆分 TasksPage；
3. 做任务卡、抽屉、补充表单、确认卡、时间线；
4. 做连接诊断；
5. 补响应式、键盘与错误态；
6. 完成真实 API 联调与三尺寸截图。

若使用宿主机前端开发：

```powershell
corepack enable
corepack pnpm --dir web install --frozen-lockfile
corepack pnpm --dir web dev
```

预期：Vite 开发地址按终端输出，API 仍由 Compose 提供。不要假设端口；以实际输出为准。

### 步骤 5：完成 P3 与全量回归

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m mypy app
corepack pnpm --dir web test:run
corepack pnpm --dir web lint
corepack pnpm --dir web build
.\.venv\Scripts\python.exe scripts\verify_oauth_end_to_end.py
```

OAuth 端到端脚本需要访问本机映射端口；若失败，先检查 Compose 端口和 Windows 防火墙。不要把未运行或因环境跳过写成通过。

---

## 10. 端到端验收剧本

### 场景 A：只读制度问答

在 Codex 和 WorkBuddy 分别输入：“员工连续请假超过三天，需要哪些审批？给我出处。”

预期：选择任务型制度工具；返回结论与来源；没有证据时明确说明；无确认弹窗、无写入。

### 场景 B：信息不全的办理意图

输入：“帮我给张三建一个试用期跟进任务。”

预期：只追问真正缺失信息；不创建业务记录；任务中心出现“待我补充”；补充后生成新 draft version。

### 场景 C：冻结草稿与确认

补充负责人和截止时间后核对确认卡。

预期：显示对象、负责人、绝对截止日期、每一步影响、风险和审批语义；提交接口不携带可被客户端替换的业务参数。

### 场景 D：审批与状态

点击“提交审批”，到团队待处理批准，再回 Agent 询问状态。

预期：`awaiting_approval → queued/running → succeeded`；任务中心与 Agent 返回一致；产生一条审批和一次副作用。

### 场景 E：重复、撤销和越权

- 连续双击提交；
- 网络超时后用同一 idempotency key 重试；
- 管理员撤销 installation 后再次提交；
- 尝试访问另一个 tenant 或权限外员工。

预期：不重复执行；撤销后失败；越权统一 403/安全错误信封；响应不泄露对象是否存在。

### 场景 F：客户端工具确认

把 Codex 工具策略设为拒绝，再调用；随后改为允许并重试。

预期：前者诊断为客户端拒绝而非 OAuth 失败；后者无需错误地重建服务端账号即可成功。

---

## 11. 最终验收清单

### 功能

- [ ] Codex 与 WorkBuddy 均完成认证、scope、业务调用三项检查；
- [ ] 自然语言能进入正确任务类型；
- [ ] 缺信息只生成草稿并补问；
- [ ] 确认对象为服务端冻结版本；
- [ ] 写操作全部经过规定的确认/审批；
- [ ] Agent 和任务中心状态一致；
- [ ] 取消、过期、失败、重试均有真实语义；
- [ ] 现有高级原子工具未破坏。

### UI/UX

- [ ] HR 用户无需理解 MCP、scope、JSON 也能完成主流程；
- [ ] 页面能分辨 OAuth、scope、客户端确认、业务工具四类问题；
- [ ] 所有状态有文字，不只靠颜色；
- [ ] 390px、1280px、1440px 不破版；
- [ ] 键盘、焦点、Esc、200% 缩放、reduced motion 通过；
- [ ] 确认按钮准确写“提交审批”；
- [ ] 空态、错误态、离线、慢请求均可恢复。

### 安全与质量

- [ ] 未授权写入为 0；
- [ ] 跨租户和对象越权为 0；
- [ ] 重复副作用为 0；
- [ ] 日志、响应、提交内容无 token/密钥；
- [ ] 分页、截断、response_format 全部符合预算；
- [ ] pytest、ruff、mypy、Vitest、lint、build 通过或有明确基线说明；
- [ ] migration 在干净数据库和已有开发数据库均成功；
- [ ] 数据流向、留存、纠正与查看权限说明完成。

---

## 12. 常见问题与解决办法

| 现象 | 最可能原因 | 排查/解决 |
| --- | --- | --- |
| `oauth-as` 反复重启 | production 未配置固定 signing key | 本地设 development 并仍配置固定 key；查 `docker compose logs oauth-as` |
| 每次重启都要重授权 | 使用临时 signing key | 检查 `OAUTH_SIGNING_KEY_PEM`，日志不得出现 ephemeral key 提示 |
| 授权成功但只见一个工具 | token 只有 baseline scope | 调 `get_my_access_profile`，比较 effective scopes，按套餐重新授权 |
| OAuth 成功但 Codex 拒绝 | 客户端工具审批策略 | 查看 Codex 当前策略并由用户放行；不要修改服务端绕过 |
| WorkBuddy `Needs authentication` | 仅发现项目 MCP，未完成该客户端 OAuth | 在 WorkBuddy 内独立发起登录，不能复用 Codex 登录状态 |
| 401 | token 缺失、过期、issuer/audience 不符或已撤销 | 查 resource metadata、AS discovery、系统时间和安装实例状态 |
| 403 | scope/角色/客户端上限/对象范围不足 | 看 access profile 与安全审计；不要通过扩角色硬解 |
| 409 | draft version 过期或并发状态冲突 | 重新获取任务详情，基于最新草稿确认 |
| 410 | 草稿已过期或任务不可恢复 | 复制为新草稿，不能复活旧 hash |
| 422 | 缺字段或格式错误 | UI 定位第一个错误字段，保留其他输入 |
| `NO_EVIDENCE` | 没有匹配资料，不是连接故障 | 提示换关键词/索引资料，诊断仍显示业务通道正常 |
| 重复任务/审批 | 客户端重试换了 idempotency key 或服务端未唯一约束 | 同一用户动作保持 key；检查唯一索引与并发测试 |
| `.sh: bad interpreter` | Git 转换为 CRLF | `core.autocrlf input`，仅修受影响文件行尾，不全仓库格式化 |
| Docker build 在中文路径失败 | 路径/共享盘编码问题 | 移到 `C:\work\HRBPilot` |
| `curl` 参数报错 | PowerShell alias | 使用 `curl.exe` |
| app 一直 starting | Alembic 正在迁移或迁移失败 | `docker compose logs app --tail 200`；先判断等待还是错误 |
| Web 看不到新代码 | 旧镜像/浏览器缓存 | `docker compose up -d --build web`，硬刷新；确认请求命中 3001 |
| 任务一直 running | worker/Celery/Redis 不健康 | 查 `worker`、`celery-worker`、`redis` 状态和最近任务 event |
| UI 状态与 Agent 不一致 | 前端缓存或投影更新遗漏 | 以服务端 agent task 为准，invalidate query；补投影一致性测试 |
| 全量测试因外部服务 skip | Compose 依赖未启动 | 按测试 marker 启动 Postgres/Redis/Milvus/MinIO；记录 skip 原因 |

---

## 13. 用户确认后才执行的 GitHub push 流程

### 13.1 硬门禁

没有收到用户明确文字确认前，只能本地 commit、查看 diff 和修复问题，**不得执行 `git push`**。

确认语句建议为：

> Windows 本地验收通过，可以 push `codex/mcp-intent-to-task-v1`。

### 13.2 push 前检查

```powershell
git status --short
git diff --check
git diff --stat origin/codex/external-assistant-mcp...HEAD
git diff origin/codex/external-assistant-mcp...HEAD
git log --oneline origin/codex/external-assistant-mcp..HEAD
git ls-files | Select-String -Pattern "env\.docker$|\.pem$|\.b64$|\.env$"
```

再运行全量门禁并保存结果。不要使用 `git add -A`，也不要按整个目录暂存。先从 `git status --short` 和 diff 生成“本次变更文件清单”，人工逐一确认后显式暂存，例如：

```powershell
git add -- app\mcp\server.py
git add -- app\scenarios\agent_tasks\service.py
git add -- tests\mcp\test_agent_tasks.py
git add -- web\src\features\agent-tasks\AgentTaskCard.tsx
# 以上只是格式示例；只暂存实际存在且已审阅的本次文件
git status --short
git diff --cached --check
git diff --cached --stat
```

逐项确认没有密钥、执行日志、截图隐私、临时数据库或无关文件。任何未审阅文件都不进入暂存区。

### 13.3 获得确认后 push

```powershell
git push -u origin codex/mcp-intent-to-task-v1
git status --short
git log -1 --oneline
```

预期：远端分支建立；本地 upstream 正确；工作区为空或只剩明确不提交的本地记录。随后在 GitHub 检查 CI，不在 CI 未完成时宣称发布完成。

---

## 14. push 后协助同步到 macOS 本地

同步前先保护当前 macOS 工作区；目前该工作区已有多份未跟踪调研/计划文档，不能直接覆盖或清理。

安全流程：

```bash
git branch --show-current
git status --short
git fetch origin
git log --oneline --decorate --graph --max-count=20 --all
git diff --stat HEAD..origin/codex/mcp-intent-to-task-v1
```

根据现场状态二选一：

1. 工作区干净：切换或 fast-forward 到远端分支；
2. 有本地内容：先把本地文档提交到独立分支或做可恢复 stash，再切换/合并；绝不 `reset --hard` 或覆盖未跟踪文件。

同步后验证：

```bash
git rev-parse HEAD
git rev-parse origin/codex/mcp-intent-to-task-v1
git status --short
docker compose up -d --build
docker compose ps
```

只有两个 revision 相同、服务健康、聚焦 smoke test 通过，才算 macOS 同步完成。

---

## 15. 回滚原则

- 应用回滚优先回到已验证提交/镜像，不删除数据库卷；
- migration 必须在开发数据库演练 upgrade；涉及数据变换时提供前向修复方案，不能假设 downgrade 无损；
- OAuth 问题可撤销单个 installation 或 client，不先做全组织紧急撤销；
- 任务执行失败保留审计与 event，不能直接删行伪装未发生；
- UI 发布异常可回退前端镜像，不影响后端任务状态；
- Git 回滚使用新 revert commit；不对共享远端执行 force push。

---

## 16. 最终交付物

1. Windows PowerShell 连接脚本与执行记录；
2. 单一 capability manifest 与漂移校验；
3. Agent task 投影、状态机、REST API 和任务型 MCP facade；
4. AI 助手接入/诊断页与任务中心 UI；
5. 后端、MCP、OAuth、前端和端到端测试；
6. 50–100 条自然语言黄金集及基线报告；
7. 数据流向与留存说明；
8. HR/HR 经理 30 分钟培训材料与示例说法；
9. Windows 验收截图和测试结果；
10. 用户确认后推送的远端分支，以及 macOS 安全同步验证记录。

这份文档是唯一开发执行主线；调研文档保留证据与案例，P0 手册保留更细的命令级排障，但不得覆盖本文件的协议基线、统一工期、UI/UX 和 push 门禁。
