# HRBPilot 0.2.0 发布文案包

![HRBPilot 发布主视觉：来源证据、保护、人工批准与可审计执行组成一条可信的 HR AI 工作流。](../../assets/hrbpilot-release-social-cover.png)

这套素材对应 [`v0.2.0`](https://github.com/kayon0209/hrbpilot/releases/tag/v0.2.0)。它面向对 HR AI、Agent 工程或可控自动化感兴趣的人；不把项目包装成已面向公众运营的 SaaS。

## 一句话定位

**HRBPilot 是一个本地优先的 HR AI 工作台：回答给证据，行动要审批，执行可审计。**

## 可用的中文发布帖

### 版本 A：GitHub / 技术社区

我发布了 HRBPilot 0.2.0：一个面向 HR 场景的本地优先 AI 工作台。

它不让 Agent 直接修改业务数据。制度问答需要引用证据；建单、分派、通知等写操作先冻结为草稿，经过人工审批后，由独立 Worker 执行并留存审计记录。项目也提供 MCP + OAuth 接入，让 Codex、Claude Code 等 Agent 在清晰的权限边界内协作。

本地完整依赖环境的最新基线：1008 个后端测试通过，Ruff/Mypy 无错误，前端 lint、构建和 73 个 Vitest 测试通过。

代码与 Release：<https://github.com/kayon0209/hrbpilot/releases/tag/v0.2.0>

### 版本 B：朋友圈 / 即刻 / 小红书

最近把个人项目 HRBPilot 推到了 0.2.0。它想解决的不是“让 AI 多会聊天”，而是 HR 场景里 AI 怎么能被放心使用：问制度要给依据；涉及建单、通知等动作必须由人批准；失败重试也不能重复发出通知。

项目支持本机 Docker 运行和 MCP 接入，不需要先买域名或上公网。欢迎看看代码、提真实 HR 场景的意见：<https://github.com/kayon0209/hrbpilot/releases/tag/v0.2.0>

### 版本 C：英文社区

I released HRBPilot v0.2.0, a local-first AI workbench for HR workflows.

The goal is not to give an agent unrestricted write access. Policy answers are evidence-grounded; write actions become frozen drafts, require human approval, then run through an auditable worker pipeline with idempotency and recovery semantics. The project also exposes a task-first MCP + OAuth interface for external agents.

The latest full local baseline passed 1,008 backend tests, static checks, and the web validation suite. Source and release notes: <https://github.com/kayon0209/hrbpilot/releases/tag/v0.2.0>

## 发布时附带的素材

- 使用本页顶部图片作为封面：[`assets/hrbpilot-release-social-cover.png`](../../assets/hrbpilot-release-social-cover.png)，尺寸为 1942×809；建议保持无文字版本，交给平台标题和贴文承载信息。
- 链接优先指向 GitHub Release；想让技术读者快速理解逻辑时，补充 [3 分钟演示](./2026-09-17-release-demo-and-showcase.md)。
- 录屏只需展示三段确定性演示：审批后建单、未批准写入被拒绝、失败后新审批重试且不重复发送。命令为 `.venv/bin/python scripts/demo_hr_case.py`。

## 发布前检查

- 移除终端、浏览器和截图中的 token、密钥、授权码和真实姓名。
- 只引用已验证数字；基线变更后重新执行 `python scripts/freeze_production_baseline.py`，不要沿用旧数字。
- 不宣称已上架 WorkBuddy、已获得企业客户或可替代 HR 判断；这些均不在当前已验证范围内。
- 本机使用不需要公网 HTTPS；只有跨设备或团队部署才需要 HTTPS、访问控制、备份和密钥轮换。

## 把反馈带回来

对项目感兴趣的 HR 从业者或工程师可以从 [HR workflow feedback](https://github.com/kayon0209/hrbpilot/issues/new/choose) 表单提交匿名化场景。表单刻意要求说明人应当批准什么、需要什么证据与审计，而不是只收集一句“加一个功能”。
