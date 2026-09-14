# 外部客户端兼容矩阵

> 更新日期：2026-09-14
> **所有"未实测"格子的含义**：没有用真实客户端验证过。不要把它读成"应该可以"。

## 如何阅读本矩阵

方案 §WP6 要求用 WorkBuddy、Codex、Claude Code 的**真实客户端**逐一验证，而不是用
curl 或单元测试推断。本轮已在本机用已安装的 Codex 与 WorkBuddy 做到“客户端发现、
注册和授权界面”级别的验证。此前浏览器停在同意页的故障已确认是授权页 CSP 拦截
跨源 loopback 回调，**不是**客户端与浏览器之间的网络命名空间隔离；服务端已为每个
已校验的回调地址精确放行其 origin。具体客户端的完整工具调用仍须逐一复测。

**已经验证的**（见 §"已完成的验证"）：协议一致性、以及用 MCP 官方 SDK 作为
"标准客户端"跑通完整链路。它证明的是**服务端正确**，不证明**某个具体客户端能连上** ——
后者的差异恰恰发生在各客户端对协议的实现取舍上（下面每一格都可能在服务端完全正确的
前提下失败）。

把未实测的格子写成"预期可用"会制造一种最坏的文档：它让下一个人跳过验证。

## 矩阵

| 能力 | WorkBuddy | Codex | Claude Code |
| --- | --- | --- | --- |
| Streamable HTTP 连接 | 已配置，待真实工具调用 | **已配置：Codex CLI 0.154.0** | 未实测 |
| 401 → 自动发现元数据 | 需要认证（未显示发现细节） | **已通过：自动检测 OAuth 支持** | 未实测 |
| 浏览器登录 + PKCE 回调 | 服务端浏览器回调回归已通过；待真实工具调用 | 服务端浏览器回调回归已通过；待原生 CLI 复测 | 未实测 |
| DCR（RFC 7591） | 未实测 | **已通过：自动 DCR 注册并进入 PKCE 授权** | 未实测 |
| CIMD 客户端身份 | 服务端已验证（e2e 第 10 节） | 服务端已验证（e2e 第 10 节） | 服务端已验证（e2e 第 10 节） |
| refresh / 重新授权 | 未实测 | 未实测 | 未实测 |
| 结构化工具结果解析 | 未实测 | 待完整登录后验证 | 未实测 |
| 403 + `insufficient_scope` 后的补授权 | 未实测 | 未实测 | 未实测 |
| 中文 Skill / 指令 | 未实测 | 不适用 | 不适用 |

## 已完成的验证

日期：2026-09-14。对象：MCP 官方 Python SDK 客户端 + 真实进程 + 真实 Postgres。

```
python scripts/verify_oauth_end_to_end.py --database-url <一次性库>
→ 74/74 项通过，退出码 0
```

覆盖：起真实进程 → 401 挑战 → 两个 well-known 变体 → AS 元数据 → JWKS → 登录 →
同意 → PKCE 兑换 → 真实 MCP 客户端 `initialize`/`list_tools`/`call_tool` → 负例
（aud 不符 / 陌生密钥 / `typ` 不符 / 非信任 issuer / 平台令牌被策略拒）→ 内省与撤销 →
refresh 轮换与重用检测 → DCR 全流程 → scope 不足的 403 → **CIMD 主路径**（https URL
作 `client_id`，本地自签 HTTPS 文档服务器 + `OAUTH_CIMD_CA_BUNDLE`）。

**它证明了什么**：服务端按规范实现，且一个遵守规范的客户端可以走完完整链路。
**它没有证明什么**：任何一个具体客户端会怎么做。

## 本机真实客户端观察

日期：2026-09-14。连接目标：`http://localhost:8001/mcp`；账户：只用于本次验证的
本地临时 HRBPilot 账号；请求范围：默认只读/建议范围，实际未调用任何业务工具。

- **Codex CLI `0.154.0`**：`codex mcp add --url ... --oauth-client-registration dcr`
  成功把服务登记为 `streamable_http`；`codex mcp login` 自动发现 OAuth、自动用 DCR
  创建 public client，并发起 S256 PKCE。授权页面成功认证并产生授权码。这证明该客户端
  与受保护资源元数据、AS 元数据、DCR、PKCE 请求参数兼容。
  曾观察到“授权码已签发、浏览器仍停在同意页”。根因是 AS 的
  `form-action 'self'` 拦截了 POST 后到 `127.0.0.1:<随机端口>` 的 302；现已改为只对
  **已校验**的回调地址放行精确 origin，并由浏览器回调回归测试守护。此前把该现象归因为
  沙箱 loopback 隔离是错误的；尚未在原生 Codex CLI 内完成最终 `get_my_access_profile` 调用。
- **WorkBuddy CLI `2.137.1`（桌面端显示 WorkBuddy 5.5.6）**：按其官方 CLI 的 HTTP
  配置方式加入项目级 `.mcp.json` 后，`codebuddy mcp get` 正确识别服务类型和 URL，并显示
  `Needs authentication`。桌面端可以承接 DCR + 浏览器 PKCE；该浏览器回调路径的 CSP
  回归已通过。当前仍只标记为“已配置，待真实工具调用”，不可宣称已完整兼容。

完整实测应在用户的本机终端（CLI 回调与浏览器同一宿主）或公网 HTTPS 测试环境执行：
完成登录后只调用 `get_my_access_profile`，再测试 token refresh、窄 scope 的 403 补授权
与一个只读结构化结果。

## 每个"未实测"格子的风险与验证方法

| 能力 | 具体风险 | 怎么验证 |
| --- | --- | --- |
| Streamable HTTP | 部分客户端只支持 SSE，或对 `Accept` 头要求更严 | 客户端里"添加远程 MCP"填 `<host>/mcp`，观察是否发起 POST 且 `Accept` 同时含两个类型 |
| 401 → 发现 | 客户端可能不读 `WWW-Authenticate`，而是硬编码 `/.well-known/oauth-protected-resource` | 抓包确认它请求的是哪个 URL；两个变体我们都提供，但**带 path 的那个**才是 RFC 9728 §3.1 的规范位置 |
| PKCE 回调 | 私有协议回调（`workbuddy://…`）在客户端侧被拒 | 先按 `OAUTH_CUSTOM_REDIRECT_SCHEMES=workbuddy` 试；若失败去掉该配置，让它回退到 loopback |
| DCR | 客户端只接受带 `client_secret` 的注册响应 | 我们的响应回 `token_endpoint_auth_method: "none"` 与 `client_secret_expires_at: 0`；确认客户端不因缺 secret 而报错 |
| CIMD | 客户端文档取不到时的回退行为与我们的实现不一致 | 用本地自签 HTTPS 文档服务器实测（脚本起服务器并信任自签根，见 ADR §13.6 / e2e 第 10 节）；真实客户端文档形状仍待 WP6 |
| refresh / 重新授权 | 客户端可能在 401 后直接要求用户重新登录，而不是用 refresh | 等 access token 过期（15 分钟）后再发一次调用，观察是否透明续期 |
| 结构化结果 | 客户端可能只读 `content[0].text`，忽略结构化字段 | 确认`user_message`（中文）出现在界面上——它是给人看的那一句 |
| 403 补授权 | 客户端可能把 403 当成永久失败 | 用窄 scope 的客户端调用一个需要更大 scope 的工具，看它是否提示"去补授权" |
| 中文 Skill | Skill 未被加载或指令被忽略 | 让它回答一个"没查到就编造"的诱饵问题，看它是否如实说"没找到" |

## CIMD 的额外说明

CIMD 是 ADR-0002 决策二指定的**主注册路径**，现已具备端到端验证：

- **单元 / 集成**（`tests/oauth/test_cimd.py`，40 项）：URL scheme、文档内 `client_id`
  与 URL 逐字节一致、redirect URI 在白名单内、缓存语义、SSRF 守卫、TLS 校验不可关闭；
- **端到端**（`scripts/verify_oauth_end_to_end.py` 第 10 节）：起一个本地自签 HTTPS
  文档服务器，把 `OAUTH_CIMD_ALLOWED_PRIVATE_HOSTS_RAW=127.0.0.1` 与
  `OAUTH_CIMD_CA_BUNDLE` 注入 AS（两者仅在 development 合法），用 https URL 作为
  `client_id` 走完 授权 → 同意 → 换令牌，并断言库里 `registration_source='cimd'`。

主路径的验证程度现已与 DCR 持平。仍**未做**的只有"用真实客户端（WorkBuddy / Codex /
Claude Code）的文档"—— 那是 WP6，不是 CIMD 本身。

## 记录要求

后续每完成一格，请在此表里写上**实际客户端版本号 + 验证日期**，并把"未实测"替换掉。
方案 §11 明确要求兼容矩阵写明实际验证版本和日期 —— 一个没有版本的"通过"是不可复现的。
