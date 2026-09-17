# Changelog

本项目采用语义化版本。`0.x` 仍属于预稳定阶段：功能可用于受控环境，但在次版本升级中可能出现需要迁移的接口调整。

## 0.2.0 — 2026-09-17

### Added

- 面向外部 AI Agent 的 MCP Streamable HTTP 接入，以及 OAuth 2.1 + PKCE 授权、发现、撤销和调用审计。
- 任务优先的 MCP 工作流：身份能力摘要、制度问答、案件上下文、冻结草稿、补充输入、提交、状态查询和取消任务。
- HR Case Agent 的人工审批、事务 Outbox、独立 Worker 派发、幂等 request ID、失败/DLQ/UNKNOWN 对账语义。
- 知识库权威性分级、租户去重、检索降级护栏和材料批处理能力。
- Web 端连接健康、权限摘要、审批队列和面向非技术用户的 MCP 接入页。
- 可复现生产基线：记录服务可达性、依赖锁哈希、后端静态检查与前端验证，并支持内容哈希校验。

### Security and reliability

- MCP 权限按用户角色能力、OAuth scope 与客户端授权上限的交集判定；写工具只创建待审批动作，不直接执行副作用。
- 授权与令牌撤销会影响后续受理；审批参数经规范化哈希绑定，已消费审批不能重放。
- 检索证据作为不可信上下文处理；输入在模型调用前经过护栏与脱敏，日志不保存原始输入。

### Verification

- 在完整本地依赖环境中冻结的基线：1008 个后端测试通过、50 个环境门控跳过；Ruff 和 Mypy 均无错误；Web lint、构建和 73 个 Vitest 测试均通过。详见 [`docs/production-baseline.json`](docs/production-baseline.json)。

### Upgrade notes

- 部署升级后执行 `python -m alembic upgrade head`，并以 `docker compose up -d --build` 更新受控 Worker 与服务镜像。
- 个人同机使用 MCP 可连接 `http://127.0.0.1:8001/mcp/tasks`；跨设备或团队部署前必须配置 HTTPS、访问控制、备份和密钥轮换。
- 不要将旧版的原子 `/mcp` 调用流程当作日常入口；新接入优先使用 `/mcp/tasks` 及任务状态闭环。

## 0.1.0

- 初始公开版本。
