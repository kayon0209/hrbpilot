# HRBPilot WorkBuddy 连接器

MCP + Skill 方案的连接器包，提交 WorkBuddy 团队审核后进入连接器市场。

## 提交前必须替换的两处

1. **`mcp.json` 里的 `url`**。当前是占位域名 `https://hrbpilot.example.com/mcp`，
   必须换成真实的对外地址（HTTPS，且在反向代理后面时要把可信代理与
   `PUBLIC_BASE_URL` 一并配好）。占位地址若被提交上去，连接器在市场里对所有人
   都连不通 —— 而且是"连不上"，不是"报一个能看懂的错"。
2. **`icon.svg`**。当前是占位图，请替换为正式图标（市场建议透明背景、小尺寸下可辨）。

## 服务端需要打开的开关

连接器不携带服务端配置，但下面两项**必须**在部署时确认，否则 WorkBuddy 连不上：

| 配置 | 取值 | 为什么 |
| --- | --- | --- |
| `OAUTH_ENABLE_DYNAMIC_REGISTRATION` | `true` | WorkBuddy 的内置 OAuth 管理器**只**走动态客户端注册（RFC 7591）；关闭时 `/oauth/register` 返回 403，授权流程在第一步就停住。 |
| `OAUTH_CUSTOM_REDIRECT_SCHEMES` | `workbuddy` | WorkBuddy 优先用私有协议回调 `workbuddy://workbuddy/mcp/connector%3Ahrbpilot/oauth/callback`。不放行时它会回退到 loopback，但那条路径依赖客户端行为，不如把首选路径直接打通。**这是需要签字的放宽**，理由与缓解见 `app/oauth/clients.py`。 |

`OAUTH_ENABLE_DYNAMIC_REGISTRATION` 默认关闭是刻意的（DCR 是公开的写端点）。
打开时请同时确认限速值与 `OAUTH_DYNAMIC_REGISTRATION_PER_HOUR`。

## 关于 `disabledTools`

`mcp.json` 关掉了 `hrbpilot_ping`：它是服务端的健康/调试探针，不带任何业务数据。
留在清单里除了让 AI 多一个"看起来能调但没意义"的工具之外，还会把调试信息暴露给
最终用户。
