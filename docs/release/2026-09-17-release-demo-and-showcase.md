# HRBPilot 发布演示与对外介绍

HRBPilot 是一个面向企业 HR 的本地优先工作台：制度问答必须给出证据，重复性材料先被结构化整理；一旦涉及建单、分派、状态更新或通知，系统把建议、审批、执行和审计拆开，始终由人承担最终判断。

这不是一个把通用聊天能力换成 HR 提示词的演示项目。它把“AI 提议”和“产生业务副作用”之间放入了可检查的审批与可恢复的执行链路。

## 已验证的版本事实

2026-09-17 对 `0cdc2c1134d1` 冻结的本地生产基线：后端 `pytest` **1008 passed / 50 skipped**、Ruff **0 errors**、Mypy **0 errors**；Web 的 lint、构建与 Vitest 均通过（**73 tests / 16 files**）。完整、带内容哈希的记录见 [`../production-baseline.json`](../production-baseline.json)，可用 `python scripts/freeze_production_baseline.py --verify` 校验。

本页只陈述已经在该仓库中验证的能力；它不是公网 SaaS 可用性声明。个人使用可在同一台电脑通过 Docker Compose 与 `localhost` 运行，不需要先申请公网域名。

## 3 分钟演示脚本

### 0:00–0:25 — 展示问题与边界

打开 README，说明两个承诺：制度结论需要引用；写动作不能由模型直接完成。强调演示只使用合成员工标识，不使用真实 HR 数据。

### 0:25–1:20 — 一个完整的受控动作

执行以下命令。它使用内存 SQLite 和确定性替身，不需要 LLM、网络或真实员工数据：

```bash
.venv/bin/python scripts/demo_hr_case.py
```

先展示“加班费争议”从制度取证、计划、人工批准到建单完成的轨迹。输出中的 `CASE_CREATED → ... → TOOL_EXECUTION_FINISHED` 是可审计事件序列，而不是聊天回复中的一句“我已完成”。

### 1:20–2:05 — 证明护栏真的会拒绝

继续展示第二段输出：没有 ApprovalRequest 的 `send_case_notification` 被拒绝。这里的重点是“模型或调用方想写”不等于“系统会写”。

### 2:05–2:40 — 证明故障不会制造重复副作用

展示第三段输出：通知 Provider 第一次超时后案件进入 `FAILED`，旧审批被消费；重试必须取得新审批和新 request ID。最终输出的“通知实际发送次数: 1”说明失败重放没有重复发送。

### 2:40–3:00 — 展示外部 Agent 接入方向

说明外部 Agent 通过 OAuth 2.1 + PKCE 连接任务优先 MCP 地址：

```text
http://127.0.0.1:8001/mcp/tasks
```

可展示 `get_my_access_profile`、`answer_policy_question`、`prepare_hr_action` 与 `get_task_status`：Agent 负责提出或查询任务，HRBPilot 负责权限、冻结草稿、审批、执行与审计。

## 一段可直接发布的介绍

> 我做了 HRBPilot：一个本地优先的 HR AI 工作台。它不把 AI 当作可以直接修改业务系统的黑箱——制度问答要带证据，涉及建单、通知等操作必须经过人工审批，并由独立 Worker 执行、全程留痕。它还提供任务优先的 MCP + OAuth 接入，让 Codex、Claude Code 等 Agent 能在清晰权限边界内协作。当前版本在本地完整环境中通过 1008 项后端测试和前端验证。

## 演示注意事项

- 不把内存演示脚本包装成真实生产数据演示；它的价值是稳定展示审批、审计和故障恢复语义。
- 若展示完整服务，先运行 `docker compose up -d --build`。`/api/ready` 可能因未配置可选 Embedding 而显示 `degraded`；数据库、Redis、Milvus、MinIO 等关键服务正常时，这不代表核心服务不可用。
- 对外截图和录屏必须遮盖令牌、密钥、真实姓名、制度原文中可识别的个人信息，以及浏览器授权码。
- 目前个人本机使用不需要公网地址；要让其他设备或团队连接时，再以 HTTPS、访问控制、备份和审计保留策略为前提部署。

## 继续迭代的顺序

1. 用 3 分钟演示收集 HR 从业者对“证据、审批、审计”是否解决真实顾虑的反馈。
2. 将最高频的一个真实任务固化为可回放的任务模板，并增加不参与提示调优的保留测试集。
3. 有明确的跨设备/团队需求后，再投入公网 HTTPS、域名和连接器上架；不要让基础设施先于用户反馈吞掉产品节奏。
