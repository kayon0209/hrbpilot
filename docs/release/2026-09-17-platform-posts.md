# HRBPilot 0.2.0 平台发布稿

统一链接：<https://github.com/kayon0209/hrbpilot/releases/tag/v0.2.0>

统一封面：[`assets/hrbpilot-release-social-cover.png`](../../assets/hrbpilot-release-social-cover.png)。发布前确认没有上传终端截图、令牌、真实 HR 数据或浏览器授权信息。

## GitHub

正式发布已完成：<https://github.com/kayon0209/hrbpilot/releases/tag/v0.2.0>。

不要重复发同样的公告。后续只在收到真实工作流反馈、发布修复或新增可验证演示时更新 Release/Discussion。

## 即刻（中文）

我把个人项目 HRBPilot 发布到 0.2.0 了。

它想解决的不是“HR AI 能不能回答问题”，而是“AI 提出行动建议后，谁来负责、如何留痕”。制度问答需要给出证据；建单、分派、通知这类写操作会先冻结为草稿，由人批准，再由独立 Worker 执行并可回看审计轨迹。

这版还加了 MCP + OAuth：Codex、Claude Code 之类的 Agent 可以在权限边界内查询、提出任务和跟踪状态，但不能绕过审批直接改业务数据。

完整本地环境刚跑过 1008 个后端测试和前端验证。项目支持个人电脑本地 Docker 运行，不需要公网域名。

想听听做 HR、HRIS 或 Agent 工程的朋友：什么场景最值得先做成“有证据、有审批、可审计”的闭环？

代码与 Release：<https://github.com/kayon0209/hrbpilot/releases/tag/v0.2.0>

话题建议：#AI #HR科技 #MCP #Agent

## 小红书（中文）

标题：**HR AI 不该直接改数据：我做了一个带审批和审计的工作台**

正文：

做 HR AI 时，我最在意的不是“回答像不像人”，而是这三个问题：

1. 这个结论有没有制度证据？
2. AI 要建单、通知、分派时，谁批准？
3. 外部服务失败后，重试会不会造成重复操作？

所以我做了 HRBPilot。

它会让制度问答带引用；让写操作先成为可查看的草稿；让人工批准和实际执行分开；再用独立 Worker、幂等 request ID 和审计事件把结果留下来。

0.2.0 还支持 MCP + OAuth，可以接入 Codex、Claude Code 等 Agent，但 Agent 没有“跳过审批直接写数据”的捷径。

这是个人项目，支持在自己的电脑上用 Docker 跑起来，不需要先有公网域名。

如果你做 HR、HRIS、合规或 AI 产品，最希望 AI 帮你处理、但又绝不能失控的一件事是什么？欢迎告诉我。

代码与演示：<https://github.com/kayon0209/hrbpilot/releases/tag/v0.2.0>

#HR科技 #AI工具 #Agent #MCP #AI产品 #独立开发

配图顺序：先用统一封面；若补充截图，只放匿名化的架构、演示输出或公开 README，不放真实员工数据。

## 知乎（中文）

标题：**做 HR AI 时，怎样让 Agent 不越权：HRBPilot 0.2.0 的审批与审计设计**

开头：

很多 HR AI 产品把重点放在“能不能生成一段看起来合理的回答”。但 HR 场景真正难的部分往往在回答之后：当 AI 建议创建案件、分派负责人、发送通知时，系统如何证明它有权限、经过了谁的批准、失败后是否会重复执行？

我在个人项目 HRBPilot 0.2.0 中，把这条链路拆成四步：证据与上下文、冻结草稿、人工审批、独立执行与审计。制度问答必须返回可引用的依据；写操作只会先生成 Proposal；审批通过后由事务 Outbox 和独立 Worker 执行；参数哈希、request ID 和事件流用于约束重放与追溯。

项目也提供了 MCP + OAuth 接入。外部 Agent 可以查询有效权限、读取有权访问的制度或案件上下文、提交任务并查询状态，但不能把自然语言指令变成未经批准的业务副作用。

这不是对 HR 判断的替代，而是把高频整理和受控执行交给系统，把判断、审批和责任留给人。

本次完整本地环境验证：后端 1008 项通过，Ruff/Mypy 无错误，Web lint、构建与 73 项 Vitest 通过。项目可在个人本机 Docker 环境运行，不需要先申请公网域名。

结尾提问：如果你正在做 HRIS、合规或 Agent 工程，最难落地的“AI 建议 → 人工决策 → 可靠执行”环节是什么？

链接：<https://github.com/kayon0209/hrbpilot/releases/tag/v0.2.0>

## LinkedIn (English)

I’ve released HRBPilot v0.2.0, a local-first AI workbench for governed HR workflows.

The interesting challenge in HR AI is not only whether a model can produce a plausible answer. It is what happens when an agent proposes a real action: creating a case, assigning an owner, or sending a notification.

HRBPilot keeps that boundary explicit:

- policy answers are evidence-grounded;
- write actions become inspectable frozen drafts;
- a human approves before any side effect;
- a transactional Outbox and independent Worker provide idempotency, auditability, and recovery semantics.

v0.2.0 also adds a task-first MCP + OAuth interface, so external agents can inspect effective access, retrieve permitted context, submit a task, and track its status without bypassing approval controls.

The latest full local baseline passed 1,008 backend tests, static checks, and the web validation suite. It runs locally with Docker Compose; no public domain is required for personal single-machine use.

I’d value feedback from HR practitioners, HRIS teams, security/compliance people, and agent builders: which HR workflow most needs an evidence → approval → audit trail today?

Release notes and source: <https://github.com/kayon0209/hrbpilot/releases/tag/v0.2.0>

Suggested hashtags: #HRTech #AI #MCP #AgenticAI #HumanInTheLoop #OpenSource
