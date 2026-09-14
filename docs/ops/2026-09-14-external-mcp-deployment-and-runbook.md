# 外部 Agent 接入：部署与运维手册

> 适用版本：ADR-0002 WP2 完成后
> 读者：负责部署与值班的工程/运维人员
> 关联：`docs/upgrade/ADR-0002-mcp-resource-server-and-authorization-server.md`、
> `docs/security/mcp-threat-model.md`

---

## 1. 这是两个进程

外部 Agent 接入由**两个独立应用**共同提供，必须分别部署：

| 进程 | 启动命令 | 默认端口 | 职责 |
| --- | --- | --- | --- |
| 资源服务器（RS） | 主应用（既有方式） | 8000 | `/mcp` 工具出口、工作台、`/api/*` |
| 授权服务器（AS） | `uvicorn app.oauth.main:app --port 8001` | 8001 | 发放与撤销令牌 |

**AS 只持有私钥，RS 只持有公钥。** RS 被完全攻破也签不出任何令牌 —— 这是 v1.0 就定下的边界，
不要在 RS 进程里加载 `OAUTH_SIGNING_KEY_PEM`。

## 2. 必需配置

### 2.1 Resource Server

| 变量 | 说明 |
| --- | --- |
| `PUBLIC_BASE_URL` | RS 对外基址（origin，不带路径）。派生 `resource`、401 挑战里的 `resource_metadata`、令牌 audience。 |
| `MCP_RESOURCE_URL` | 未显式配置时由 `PUBLIC_BASE_URL` 派生为 `<base>/mcp`；**必须与 AS 侧算出的 canonical resource 逐字节一致**。 |
| `MCP_AUTHORIZATION_SERVERS` | **信任列表**：逗号分隔的 AS issuer。留空的 RS 不会接受任何 AS 令牌，但也不会报错 —— 症状是"外部 Agent 全部 401"。生产要求公网 https。 |
| `MCP_ACCEPTS_PLATFORM_TOKENS` | 过渡开关。AS 上线并验证后置 `false`，`/mcp` 就只接受 audience 绑定到 MCP resource 的令牌。 |
| `MCP_EXTERNAL_ENABLED` | **回滚总开关**。置 `false` 后 `/mcp` 返回 503（不是 401），工作台与 `/api/*` 不受影响。 |

### 2.2 Authorization Server

| 变量 | 说明 |
| --- | --- |
| `OAUTH_ISSUER` | AS 的对外身份。生产必须是公网 https。 |
| `OAUTH_SIGNING_KEY_PEM` | ES256（P-256）私钥。**生产环境未配置会启动失败** —— 宁可起不来，也不要"能启动但签不出令牌"。 |
| `OAUTH_ROTATED_PUBLIC_KEYS_PEM` | 轮换窗口内仍有效的旧公钥（只放公钥）。 |
| `OAUTH_ENABLE_DYNAMIC_REGISTRATION` | 默认 `false`。**WorkBuddy 的内置 OAuth 管理器只走动态注册**，接入它时必须打开。 |
| `OAUTH_CUSTOM_REDIRECT_SCHEMES` | 默认空。接入 WorkBuddy 时可设为 `workbuddy`（理由与缓解见 `app/oauth/clients.py`）。 |
| `OAUTH_ACCESS_TOKEN_TTL_SECONDS` | 默认 900。WorkBuddy 建议 3600，但更短只会更安全（refresh 会兜住），不用改。 |
| `OAUTH_REFRESH_TOKEN_TTL_DAYS` | 默认 30。WorkBuddy 要求 ≥ 30 天。 |

### 2.3 反向代理与可信代理

**这一步不做，限流会形同虚设。** `request.client.host` 在反向代理后面取到的是**代理的
地址**，于是所有用户共用一个桶。

- 启动 uvicorn 时加 `--proxy-headers --forwarded-allow-ips=<代理地址>`；
- 只信任你**确实**控制的代理。`X-Forwarded-For` 是客户端可以伪造的头；把它无条件地
  当成真实来源，等于把限流键交给攻击者；
- 不要推导 `Host`：`PUBLIC_BASE_URL` / `OAUTH_ISSUER` 都是显式配置（ADR-0002 §5）。
  靠请求头推导对外地址，会让一次错误的代理配置静默地把客户端导向错误的授权服务器。

`/mcp` 与 AS 都是**独立于工作台**的对外面。建议在代理层就把它们与 `/api/*` 分开，
这样"关闭外部接入"可以只在代理层做，不需要动工作台。

## 3. 端点清单

| 端点 | 谁提供 | 匿名可读 |
| --- | --- | --- |
| `/.well-known/oauth-protected-resource`（含 `/mcp` 变体） | RS | 是（协议要求） |
| `/.well-known/oauth-authorization-server` | AS | 是（协议要求） |
| `/.well-known/jwks.json` | AS | 是（只含公钥） |
| `/oauth/authorize` | AS | 是（授权流程入口） |
| `/oauth/token` | AS | 是（凭 `client_id` + PKCE） |
| `/oauth/register` | AS | 是（默认关闭时返回 403） |
| `/oauth/revoke` | AS | 是（RFC 7009） |
| `/oauth/introspect` | AS | 是（RFC 7662，需已注册 `client_id`） |
| `/mcp` | RS | 否（401 + RFC 9728 挑战） |
| `/api/admin/mcp/*` | RS | 否（需 `mcp_admin` 能力） |

## 4. 数据库迁移

```bash
alembic upgrade head     # 038 → 039 → 040
alembic downgrade -1     # 回退一步
```

三张新表与四列：

- `oauth_clients` / `oauth_authorization_codes` / `oauth_tokens` / `oauth_revoked_tokens`
  —— **刻意不上 RLS**（每次查询都发生在还不知道租户是谁的时刻，见 `app/data/models/oauth.py`）；
- `mcp_call_audits` —— **启用并强制 RLS**（写入时租户已确定）；
- `approval_requests` 新增 `requester_user_id` / `client_id` / `installation_id`。

**回滚演练**：`alembic downgrade 037_chunk_page_number` 会删掉上面全部内容。演练请在
副本库上做，并确认回退后工作台的登录与审批数据不受影响（它们不在这些表里）。

## 5. 回滚（紧急关闭外部接入）

顺序**不能颠倒**：

1. `MCP_EXTERNAL_ENABLED=false`，重启 RS。门先关上 —— `/mcp` 立即返回 503。
2. 撤销已签发的凭据。租户级用管理端 API；跨租户或管理端不可用时用脚本：

   ```bash
   # 先预览（默认 dry-run）
   python scripts/revoke_all_mcp_installations.py
   # 确认后执行
   python scripts/revoke_all_mcp_installations.py --apply
   ```

3. 若 AS 也需要下线，停掉 AS 进程。**注意**：RS 会用本地 JWKS 验签，所以"停掉 AS"
   不会让已签发的令牌立刻失效 —— 撤销（第 2 步）才是那个动作。

反过来做（先撤销、后关开关）的话，在进程交替的那段时间里新授权仍会被接受。

工作台的登录、审批、案件数据全程不受影响。

## 6. 故障排查

### 6.1 客户端连不上

按**发现链路的顺序**排查，不要跳步：

1. `curl -i https://<host>/mcp` → 应得到 **401** 且 `WWW-Authenticate` 含
   `resource_metadata`。得到 403/404/200 说明传输层就不对，客户端根本没有可用的起点。
   得到 **503** 说明 `MCP_EXTERNAL_ENABLED=false`。
2. 取该 URL → 应得到 RFC 9728 文档，其中 `resource` 与 `authorization_servers` 齐全。
   `resource` 必须与 AS 侧算出的 canonical resource **逐字节一致**（差一个斜杠也不行）。
3. 取 `authorization_servers[0]/.well-known/oauth-authorization-server` → 应得到
   RFC 8414 文档且 `issuer` 与请求的 issuer 一致。
4. 取 `jwks_uri` → 应得到公钥集合，且**不含** `d` 字段。

第 2、3 步任一失败，问题都在配置（`PUBLIC_BASE_URL` / `OAUTH_ISSUER` /
`MCP_AUTHORIZATION_SERVERS`），不在客户端。

### 6.2 常见症状对照表

| 症状 | 最可能的原因 | 看哪个日志事件 |
| --- | --- | --- |
| 全部 401，日志里没有 `mcp_token_rejected` | `MCP_AUTHORIZATION_SERVERS` 没配 | —— |
| 401 且记 `mcp_token_rejected` | 令牌被拒（详见 `reason` 字段） | `mcp_token_rejected` |
| 401 且记 `mcp_platform_token_refused_by_policy` | `MCP_ACCEPTS_PLATFORM_TOKENS=false`，客户端在用平台令牌 | `mcp_platform_token_refused_by_policy` |
| 403 + `insufficient_scope` | 凭据的有效 scope 不含所需范围 | `mcp_tool_denied`（`reason` 为 `missing_scope` / `client_ceiling`） |
| 429 | 限流 | `mcp_rate_limited`、`oauth_rate_limited`、`oauth_token_rate_limited` |
| 用户登录后仍 401 | 登录租户与令牌租户不一致（当前登录固定为 `default`） | `oauth_as_login_*` |
| 审批通过后动作未执行 | 发起方的授权已被撤销 | `approval_blocked_installation_revoked` |
| 审计表没有新行 | 审计写入失败（不影响调用本身） | `mcp_audit_persist_failed` |

### 6.3 令牌被拒的原因分类

`mcp_token_rejected` 的 `reason` 字段是稳定枚举：

- `malformed` —— 解析或验签失败（也可能是 RS 取不到 JWKS）；
- `wrong_type` —— 不是 access token（例如拿 refresh token 来调 `/mcp`）；
- `incomplete` —— 缺必需声明；
- `revoked` —— **策略动作**，不是凭据损坏。看到它说明有人或某个流程撤销了授权。

把这四类混成一条"验证失败"会让排查方向整个偏掉 —— 所以它们是分开的。

## 7. 告警建议

没有独立的指标后端时，**用结构化日志做告警**。下面这些事件名可以直接作为告警规则的条件：

| 优先级 | 事件 | 为什么 |
| --- | --- | --- |
| P0 | `mcp_audit_persist_failed` | 审计断了。调用仍在继续，但证据没了 —— 这是最容易被忽略的静默故障。 |
| P0 | `oauth_refresh_reuse_detected` | refresh token 重放。意味着凭据可能已泄漏。 |
| P0 | `approval_blocked_installation_revoked` | 已撤销的 Agent 仍在尝试操作 —— 说明撤销没有按预期生效，或有人在重放。 |
| P1 | `mcp_rate_limited` / `oauth_rate_limited` 突增 | 可能是失控客户端，也可能是探测。按 `client_id` 维度看是哪一个。 |
| P1 | `mcp_tool_denied` 突增 | 有人在试探边界。按 `tenant_id` + `tool` 聚合。 |
| P1 | `as_metadata_issuer_mismatch` | AS 元数据声明的 issuer 与我们请求的不一致 —— DNS 劫持或反代配错。 |
| P2 | `oauth_dynamic_client_registered` 突增 | DCR 滥用。 |
| P2 | `mcp_external_disabled` | 回滚开关被打开了。可能是故意的，也可能是配置漂移 —— 需要人确认。 |

字段说明：`mcp_tool_denied` 带 `surface`（`mcp_protocol` / `mcp_transport_guard` /
`mcp_rest_bridge`），用来分辨拒绝发生在哪一层。

### 尚未实现（明确列出）

- **没有独立的指标端点**（Prometheus / OpenTelemetry）。上面的告警完全依赖日志采集。
- **没有异常 DCR 的自动阻断**：只有限速，没有"这个客户端看起来不对劲就自动禁用"。
- **内省端点与 403 拒绝本身没有单独的计量**，只能从日志计数。

## 8. 密钥轮换

1. 生成新密钥，把**旧公钥**放进 `OAUTH_ROTATED_PUBLIC_KEYS_PEM`；
2. 重启 AS（新密钥生效，JWKS 同时包含新旧公钥）；
3. 等待 **≥ JWKS 缓存时长（300 秒）+ 最长令牌有效期（900 秒）**；
4. 从 `OAUTH_ROTATED_PUBLIC_KEYS_PEM` 移除旧公钥并重启。

**轮换过程尚未在真实进程里演练过**（只有实现与单测）。首次执行请安排在低峰期，并按
第 6.1 节逐步验证。应急撤销一把泄漏的私钥就是上面的第 1—2 步，加上第 5 节的凭据撤销。

## 9. 月度检查

- [ ] `MCP_AUTHORIZATION_SERVERS` 与 AS 实际 issuer 一致；
- [ ] `MCP_ACCEPTS_PLATFORM_TOKENS` 是否已经可以置 `false`；
- [ ] `GET /api/admin/mcp/installations` 里是否有不再使用的安装实例（应撤销）；
- [ ] `mcp_call_audits` 的增长速率与保留策略；
- [ ] 轮换窗口里的旧公钥是否已到可以移除的时间。
