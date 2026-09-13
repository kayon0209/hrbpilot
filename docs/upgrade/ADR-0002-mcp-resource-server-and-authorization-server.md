# ADR-0002：HRBPilot 作为 MCP Resource Server，及其授权服务器选型

- 状态：**已接受**（决策部分）。WP0 决策已定；WP1 已实施；**WP2-a（RFC 9728 发现面）已实施**（§12）；
  WP2 其余部分（AS 落地、CIMD 校验、外部令牌签发与校验）尚未实施。
- 日期：2026-09-13（§13 记录了 WP2-a 的实施与两处对 §5 措辞的修正）
- base SHA：`868c6aecb80d436f5faeb2858d46a0cda018329c`
- 上游输入：`docs/plans/2026-09-13-external-assistant-mcp-execution-plan.md`
- 相关：`docs/upgrade/ADR-0001-single-bounded-agent.md`、`docs/security/mcp-threat-model.md`

## 1. 背景：这条边界现在的真实状态

外部 AI Agent 要实时调用 HRBPilot，需要一个标准 MCP + OAuth 的边界。核验后的现状（全部有据可查）：

| 事实 | 位置 |
| --- | --- |
| `/mcp` 在传输层被认证、RBAC、限流三层整体跳过 | `app/access/middleware/auth.py:37-44`、`rbac.py:170-176`、`rate_limit.py:23,37-39` |
| 未认证调用返回业务信封 `AUTH_REQUIRED`，而非传输层 401 | `app/mcp/read_dispatch.py:69-84` |
| 唯一的凭据是平台自签 JWT：**HS256 对称密钥**，`issuer == audience == "hrbp-ai-workbench"`（都不是 URL） | `app/config/settings.py:22-31` |
| 没有 JWKS、没有密钥轮换落点 | 全仓无 `.well-known` / JWKS 路由 |
| access token 的校验规则曾写两遍（中间件 + MCP 出口） | 已由本次合并到 `app/access/tokens.py` |
| 授权判定曾写两遍，且结论相反 | 已由本次合并到 `app/mcp/auth/authorization.py` |
| 完全没有公开 URL / 域名配置 | `settings` 里只有 `cors_allowed_origins` 指向 localhost |

最后一条是关键前置：**OAuth 的 issuer、resource、audience 都是 URL，而本仓库当前没有任何地方表达"这个服务对外的地址是什么"。** 这是 WP2 的第一道工作，不是配置细节。

## 2. 决策一：角色定位 —— HRBPilot 只做 Resource Server

**决定**：HRBPilot 在 MCP 边界上**只**承担 OAuth 2.1 Resource Server 的职责：

1. 暴露 `/.well-known/oauth-protected-resource`（RFC 9728）；
2. 校验每个请求的 bearer token（签名、`iss`、`aud`、有效期、撤销）；
3. 未携带或无效时返回传 输层 401 + `WWW-Authenticate` 挑战。

**不**在 `/mcp` 内实现授权端点、令牌签发或同意页。

**理由**：现行 MCP 授权规范（2026-07-28）明确把三个角色分开，MCP server 侧"你只负责三件事：校验令牌、提供元数据、给出正确的挑战"。把签发逻辑放进被保护的进程里，会把密钥的爆炸半径与业务服务的爆炸半径合并；而 HRBPilot 是承载员工敏感数据的服务，这个合并是不划算的。

**代价与边界**：规范也允许 AS 与 RS 由同一实体托管。因此本决策禁止的是"逻辑耦合"，不是"同仓部署"。ASP.NET 允许的部署形态见决策五。

## 3. 决策二：客户端注册 —— CIMD 优先，DCR 降为兼容（**与上游方案相反**）

上游方案 §4.2 写的是"DCR 作为 WorkBuddy 的明确需求……同步支持 Client ID Metadata Documents，或在 ADR 中记录暂不支持的客户端影响"。也就是说方案把 DCR 当主路径、CIMD 当补充。

**现行规范是反过来的**，原文：

> Authorization servers and MCP clients SHOULD support OAuth Client ID Metadata Documents. … Dynamic Client Registration is deprecated and retained for backwards compatibility.
> —— MCP Authorization Specification, revision 2026-07-28

**决定**：

| 机制 | 优先级 | 首版是否实施 |
| --- | --- | --- |
| Client ID Metadata Documents（CIMD） | 主路径（SHOULD） | 是 |
| 预注册（pre-registration） | 备选 | 是（企业内部固定客户端） |
| Dynamic Client Registration（RFC 7591） | **兼容回退（MAY，已弃用）** | 是，但仅当真实客户端实测证明必需 |

**判定规则（避免凭宣传决定）**：WP6 的真实客户端认证里，逐个客户端抓取其注册行为。
若某客户端**只**会 DCR，则为其保留 DCR 并**限速 + 限 redirect scheme**；
若客户端支持 CIMD，则**不**为它开启 DCR。不允许"为了保险两边都开"。

**依据（外部数据，非推断）**：一项针对 119 个启用 OAuth 的 MCP 服务器的研究显示
**119/119 至少存在一项授权缺陷**，其中 **96.6% 的受测服务器存在 DCR 相关缺陷**
（arXiv:2605.22333，转引自 2026 年现场指南）。这是把 DCR 从主路径上撤下来的实证理由，
而不仅是规范文字。

**这条偏差按上游方案自己的要求记录**：方案 §14 写着"若规范与本文冲突，以当时正式规范、
安全要求及真实客户端测试为准，并在 ADR 中记录偏差"。此处即为该偏差。

## 4. 决策三：令牌体系保持双轨，不合并

**决定**：

| 用途 | 凭据 | 签名 | `iss` / `aud` |
| --- | --- | --- | --- |
| 网页登录会话（HR 工作台 UI、`/api/*`） | 现有平台自签 JWT | HS256（现状不变） | `hrbp-ai-workbench` |
| 外部 Agent 调用 `/mcp` | AS 签发的 access token | **非对称（ES256 或 RS256）+ JWKS** | `https://<host>` / `<canonical MCP resource URI>` |

Resource Server 按 `iss` 分流校验；**内部会话令牌在 `/mcp` 上不被接受**（audience 不匹配）。

**理由**：

- 对称密钥无法做到"只签发不校验能力"，也无法轮换旧 key 的验证窗口（WP8 要求）。
  外部 Agent 的令牌必须 audience-bound，这要求非对称签名与可发布的 JWKS。
- 合并成一套的代价是：要么让网页会话令牌也能调 `/mcp`（等于取消了 audience 绑定，
  正是 2026-07-28 规范要堵的那个洞），要么给所有网页用户补一次 OAuth 授权流程
  （与本次目标无关的破坏性改动）。两者都不划算。

**副作用（已主动收口）**：REST 桥 `/api/mcp` 只接受内部凭据。这不是缺口，是设计 ——
它是**工作台 UI 的内部接口**，不是外部 Agent 的入口。`app/access/routes/mcp.py`
的 `_principal()` 现在显式要求 `auth_method == "internal"`，对任何非自签来源
fail-closed 返回匿名。将来把 OAuth 令牌接进这条桥时，必须先让请求携带完整主体
（含客户端与授权上限），否则会把客户端上限静默丢掉。

## 5. 决策四：issuer / resource / audience 的取值来源

**决定**：新增一个显式配置项表达对外基址（形如 `public_base_url`），并由此派生：

```
issuer                          = https://<public_base_url>/oauth        # AS 自身标识
mcp resource (canonical)        = https://<public_base_url>/mcp
authorization server metadata   = https://<public_base_url>/.well-known/oauth-authorization-server
protected resource metadata     = https://<public_base_url>/.well-known/oauth-protected-resource
```

**约束**：

- 三者必须是**绝对 HTTPS URL**，且 `iss` 与 metadata 文档中的 `issuer` 逐字节一致
  （RFC 8414 / RFC 9207 的 mix-up 防御前提）。
- `resource` 参数在授权请求与令牌请求上都是 **MUST**（RFC 8707），签发出的令牌
  `aud` 必须等于 canonical MCP resource URI；两者不一致即拒绝。
- **该配置项已由 WP2-a 引入**（`settings.public_base_url`，默认 `http://localhost:8000`），
  并同时引入校验。实际实现的规则见 §5.1（与本节原措辞有两处出入，已在 §12 记录）。
- 生产形态下 `public_base_url` 必须是显式配置的**公网 https origin**；默认值只指向
  localhost，因此它永远不可能静默指向某个真实域名 —— 这是默认值唯一被允许的形态。

**未决**：是否用可信代理的 `X-Forwarded-Host` 推导基址。当前决定是**不**推导 ——
Host 头是客户端可控输入，用它在签发路径上生成 issuer 是典型的可被投毒的设计。
基址必须来自配置。（WP8 的 Host/Origin 校验另议。）

### 5.1 实际实现的校验规则（WP2-a）

实现位置：`app/config/settings.py` 的 `_normalize_public_base_url` / `_parse_authorization_servers`。

| 输入 | 结果 |
| --- | --- |
| `https://hrbp.example.com` / `https://hrbp.example.com:8443` | 接受（端口保留） |
| `http://localhost:8000/` | 接受，归一化为 `http://localhost:8000` |
| 首尾空白 | 接受，trim 掉 |
| 内部空白 / 非 http(s) scheme / 相对形式 | 拒绝 |
| 含路径（如 `/mcp`） | 拒绝 |
| 含 query / fragment / userinfo | 拒绝 |
| `http://` + 非环回主机 | 拒绝 |
| staging/production + 非公网 https origin | 拒绝 |

**两处对 §5 原措辞的修正**（按 §14 的偏差记录要求记下）：

1. **尾斜杠改为归一化，不再启动失败。** 原措辞是"带尾斜杠……一律启动即失败"。
   但 `http://localhost:8000/` 语义毫无歧义，让它失败只会制造一次无意义的部署中断。
   判据应该是"这个写法是否改变语义"：**路径**会改变 RFC 9728 §3.1 的 well-known
   解析位置，所以那一条严格拒绝；尾斜杠不会，归一化即可。
2. **生产强制公网 https 是新增的**，原措辞只说"不允许默认值指向真实域名"。
   理由：这个值会出现在元数据与令牌 audience 里，是**对外身份**。生产留 localhost
   会让元数据声明一个任何客户端都用不上的身份，且是静默的 —— 服务照常启动、照常
   返回 200，只有客户端在别处失败。这与 `vector_db_host` 一类**内部连接地址**性质
   不同（后者外部看不到，localhost 在生产是正常的），所以只有前者受此约束。

**这条约束的代价（已知并接受）**：既有生产部署若不设 `PUBLIC_BASE_URL`，升级后会
启动失败。这是**有意**的：错误信息明确写着该配什么，比"元数据静默指向 localhost"
好。该行为由 `tests/rag/test_security_regressions.py` 的三个用例锁定（含两个负例）。

## 6. 决策五：AS 实现选型 —— Authlib，部署形态先同仓独立应用

**候选对比**（数据取 2026-09-13，来源见文末）：

| 方案 | 维护状态 | 许可 | 协议覆盖（本需求相关） | 数据库适配 |
| --- | --- | --- | --- | --- |
| **Authlib** | v1.7.2，2026-05-06 发布；月下载 1.55 亿；PyPI 标记 Active、无已知漏洞；GitHub 5.3k star / 115 open issues | BSD-3-Clause（另有商业许可，可选） | RFC 6749 / 6750 / 7009 撤销 / 7523 / **7591 DCR** / 7592 / **7636 PKCE** / 7662 introspection / **8414 AS metadata** / 8628 / 9068 / 9101 / **9207 issuer identification** | 不绑定 ORM，模型由使用方提供 → 可用现有 SQLAlchemy + Alembic |
| 外部 IdP（Keycloak / Zitadel 一类） | 由各项目自身发布节奏决定 | 各自不同（Apache-2.0 等） | 覆盖最全（含企业 SSO、多 IdP 联合） | 自带数据库，需独立运维 |

**决定**：**用 Authlib 实现 AS，且作为同一仓库内的独立应用（独立入口、独立进程、独立密钥环境变量），不要嵌进 `/mcp` 的进程内。**

**理由**：

1. 本仓库已有 SQLAlchemy + Alembic + 多租户 RLS 的成熟约定。自建 AS 的模型
   （`mcp_clients` / `mcp_installations` / 授权码 / refresh 会话）可以直接落进这套约定，
   与 `tenant_id` 的隔离机制天然同构；换成外部 IdP 反而要做一次租户映射。
2. Authlib 覆盖了本需求**全部**协议点，且不要求使用它的 ORM/框架适配层
   （不绑 Flask/Django，可与 FastAPI 共存），因此不会为引入它而改造现有应用。
3. 独立进程而非同进程：AS 的可用性要求与 HR 工作台不同（AS 故障不应影响内部办公），
   且密钥环境变量可以分开。规范允许同实体托管，因此这不是规范要求，是故障域考虑。
4. `python-jose` 已在依赖中且 `authlib>=1.7` 已迁移到 `joserfc`，二者不冲突；
   新增依赖只有 Authlib 及其传递依赖 `cryptography`、`joserfc`。

**已知缺口（Authlib 不覆盖，需自行实现）**：RFC 9728 Protected Resource Metadata 与
Client ID Metadata Documents 的**文档获取与校验**。前者是一份静态 JSON，后者是"按 URL
取文档并校验 `client_id == URL`"的 HTTP 客户端逻辑。两者都**不涉及密码学实现**，
因此不违反上游方案"不得手写密码学实现"的约束；但其校验逻辑必须按规范逐条实现并配负例测试
（见威胁模型 T-2、T-6）。

**切换到外部 IdP 的触发条件**（满足任一即重新评审本决策）：

- 需要接入企业既有身份源做 SSO（而不只是 HRBPilot 自己的账号体系）；
- 需要多个相互独立的授权服务器联合；
- 组织已有统一身份平台，且其运维成本已摊薄。

## 7. 决策六：ADR 与威胁模型落在既有目录

**决定**：ADR 放 `docs/upgrade/`（与既有 `ADR-0001-single-bounded-agent.md` 同处），
威胁模型放新建的 `docs/security/`。

**理由**：上游方案 §5 指定 `docs/adr/`，但该目录不存在，而 ADR-0001 就在 `docs/upgrade/`。
按方案字面执行会造出**第二套约定**，正文里已存在的 ADR 反而更难被找到。
方案自身也写了"候选范围……执行 Agent 应先通过现有结构确认，避免为了匹配文档而制造不必要目录"。

## 8. 决策七：门禁口径用 CI 真实命令，而不是方案 §6.1 的四条

**决定**：本次及后续工作包以 `.github/workflows/ci.yml` 的命令为准：

```bash
ruff check app tests evaluation
ruff format --check app tests
mypy app
pytest -q
```

**理由（实测证据，非偏好）**：方案 §6.1 给的是 `ruff check .` 与 `ruff format --check .`。
在**未改动的基线上**实测：

| 命令 | 基线结果 |
| --- | --- |
| `ruff check .` | **退出码 1**，4 个错误全部在 `scripts/fault_injection_matrix.py` |
| `ruff format --check .` | **退出码 1**，9 个文件待格式化，落在 `docs/*.md`、`scripts/`、`evaluation/` |
| `ruff check app tests evaluation` | 退出码 0，All checks passed |
| `ruff format --check app tests` | 退出码 0，335 files already formatted |

差异来源：`.` 把 `docs/` 的 Python 代码块与 `scripts/` 一起纳入，而 CI 从不检查它们。
若照方案执行，WP1 会被迫顺带重排 9 个无关文件 —— 正是方案 §10 明确禁止的"生成式大提交"。
故按 CI 口径执行，并在此记录偏差。

## 9. 决策八：`McpPrincipal.installation_id` 允许为空

**决定**：`installation_id: UUID | None`，仅平台自签凭据可为 `None`；`oauth` / `pat`
主体必须同时给出 `installation_id` 与 `client_ceiling`，否则**构造失败**。

**理由**：平台自签凭据不属于任何客户端安装实例。上游方案 §3.1 的字段表写的是
`installation_id: UUID`（非空），但为它伪造一个 UUID 会让审计把平台凭据读成
"某个真实客户端"，从而污染按客户端/安装实例分组的审计与撤销入口。
`None` + `auth_method == "internal"` 才是诚实的编码。
反过来，把"外部凭据必须带上限"做成**构造期约束**而不是运行时提醒，
是为了让 WP2 漏填上限时当场失败，而不是静默放行。

## 10. 决策九：对象级 ACL 留在执行期，但必须被显式标注

**决定**：`authorize_tool_call` **不**评估对象级 ACL。决策对象带一个固定字段
`object_acl = "must_be_rechecked_at_execution"`，并由测试锁定。

**理由**：对象级可见性需要一次数据库读取（`app/access/object_scope.py` 的
`resolve_visible_user_ids` 会按角色查 `ManagerOrgScope`／`User`），无法对主体做纯函数
判定；项目既有约定也是放在 `HRCaseService` 执行期。上游方案 §3.1 的交集公式里包含
`object_acl`，所以本决策**不声称**该维度已覆盖，而是把它标注为未评估 ——
"没做但看起来做了"比"明确标注没做"危险得多。WP7 引入案件工具时，接在这个字段上。

## 11. 后果

**正面的**：

- WP1 已经消除了两条出口结论相反的越权面，并把判定收敛为一个纯函数（可穷举测试）。
- 三个维度（角色能力 / scope / 客户端上限）各有独立、可定位的拒绝原因码，
  审计能区分"人不够"与"这个客户端不够"。
- 令牌校验只剩一处，WP2 换签名体系时不存在"改了一处漏了一处"。
- ADR 与威胁模型的落点与既有约定一致，不产生第二套目录。

**代价与遗留**：

- 内部会话令牌在 `/mcp` 上不可用之后，现有基于 JWT 的 MCP 手动联调方式会失效 ——
  这是预期的，WP5 的 Skill 包与 WP6 的客户端认证将改走 OAuth。
- 双轨令牌意味着 Resource Server 需要按 `iss` 分流，多一处分支（有测试覆盖）。
- RFC 9728 与 CIMD 需自行实现，属于本次新识别的、方案未列出的工作量。

**WP2 的前置条件（全部满足才可开工）**：

1. 新增 `public_base_url` 配置及其启动期校验；
2. 确定 AS 的部署入口（独立进程）与密钥环境变量名；
3. `authlib` 加入依赖并在真实 Python 3.12（CI 版本）上验证；
4. RFC 9728 / RFC 8414 / RFC 9207 三份元数据与 `iss` 的取值在本 ADR 第 5 节冻结。

## 12. 实施记录：WP2-a（RFC 9728 发现面）

**范围**：只做 Resource Server 的**发现面**，不碰授权服务器。§11 列出的四项 WP2 前置
条件中，本工作包完成第 1 项（`public_base_url` 配置及其启动期校验）与第 4 项
（issuer / resource / metadata 取值冻结）。第 2、3 项（AS 部署入口、Authlib 依赖）
属后续工作包。

### 12.1 交付物

| 文件 | 作用 |
| --- | --- |
| `app/config/settings.py` | `public_base_url` + `mcp_authorization_servers` + `mcp_accepts_platform_tokens`，含启动期校验与三个派生 URL |
| `app/access/resource_metadata.py` | RFC 9728 文档与 `WWW-Authenticate` 挑战的**纯函数**构造 |
| `app/access/protocol_paths.py` | `WELL_KNOWN_PREFIX`，零依赖，供三层中间件共用 |
| `app/access/routes/well_known.py` | 两个匿名端点（根变体 + 带 path 变体） |
| `app/access/middleware/auth.py` | `/mcp` 改为挑战式认证（原为整体跳过） |
| `tests/mcp/test_resource_metadata.py` | 27 条断言，含 5 条负例 |
| `tests/rag/test_security_regressions.py` | 补 `_production_settings` helper + 两个生产负例 |

### 12.2 行为变更：`/mcp` 的未认证响应

| | 变更前 | 变更后 |
| --- | --- | --- |
| 无凭据 | `200` + 业务信封 `AUTH_REQUIRED` | **`401`** + `WWW-Authenticate: Bearer resource_metadata="…"` |
| 凭据不可用 | 同上（或无差别处理） | `401` + `error="invalid_token"` + `resource_metadata` |

这是**必要的破坏性变更**：MCP 客户端的授权自动发现完全依赖那个 401。原先的 200 +
信封对客户端既不是成功也不是挑战，它只能放弃。

**两种传输的差异是有意的**（不是不一致）：

- **HTTP `/mcp`**：传输层返回 401。请求根本到不了工具层。
- **stdio**：没有状态码，读工具继续返回 `AUTH_REQUIRED` 信封。

必须一致的只有**判定**，那由 `authorize_tool_call` 保证（WP1）。`anonymous_read_envelope`
现在只服务 stdio，已在 `app/mcp/read_dispatch.py` 与 `app/mcp/server.py` 就地标注。

### 12.3 实施中发现并修正的问题

**三层中间件各有匿名路径清单 —— 改一层不够。** `/.well-known/` 只加进认证层后，
请求被**限流层**以 `403 FORBIDDEN "Missing user context"` 拒掉，而认证层日志显示
"已正常放行"。症状指向了错误的那一层。

处理：抽出零依赖的 `app/access/protocol_paths.py`（不能放进 `resource_metadata.py`
—— 那个模块导入 `scopes`，而 `scopes` 导入 `rbac`，`rbac` 引用该常量会成环），
三层共同引用；并加一条断言"三层都放行发现端点"的测试作为守卫。
各层**其余**匿名条目（health / webhooks / dev-users）语义不同，**未合并** ——
合并会把"要求认证"的路径意外放宽成匿名，那是安全方向的变更，必须逐条审查。

**新增的生产必需配置打破了既有的测试契约。** `test_production_accepts_strong_jwt_secret`
断言的是 JWT secret，却因缺少 `public_base_url` 而失败 —— 症状同样指向错误的地方。
处理：按该文件**已有**的模式（`REAL_MASTER_KEY` 常量 + 注释）抽 `_production_settings`
helper，把"生产必需配置"收成一处，并补两个负例（环回、明文 http）。

### 12.4 未做的事（明确列出，避免被读成已完成）

- **AS 未落地**：`mcp_authorization_servers` 默认为空，元数据**省略**
  `authorization_servers` 字段。客户端会发现到这一步就停住 —— 这是诚实的表达
  （空数组语义未定义；编造一个不存在的 AS 地址会让客户端去请求 404，更难诊断）。
- **外部令牌不可签发**：`/mcp` 目前仍只接受平台自签 JWT，由过渡开关
  `mcp_accepts_platform_tokens`（默认 `true`）控制。AS 上线后翻成 `false`，
  届时 ADR §4「内部会话令牌在 /mcp 上不被接受」由
  `test_platform_tokens_can_be_switched_off_for_the_mcp_surface` 强制。
- **CIMD 文档获取与校验**未实现（T-6）。
- **元数据端点的 `Cache-Control` 与独立限流**未设置（T-11 缺口）。

### 12.5 门禁（改动前后实测，CI 口径）

| 口径 | WP1 后 | WP2-a 后 |
| --- | --- | --- |
| `pytest -q` | 583 / 0 / 50 | **612 / 0 / 50**（+29 全为新增） |
| `ruff check app tests evaluation` | exit 0 | exit 0 |
| `ruff format --check app tests` | exit 0（343） | exit 0（347） |
| `mypy app` | exit 0（235） | exit 0（238） |

## 13. 来源

- MCP Authorization Specification，revision 2026-07-28：<https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/>
- MCP Client Registration（CIMD / 预注册 / DCR 优先级与弃用说明）：<https://modelcontextprotocol.io/specification/draft/basic/authorization/client-registration>
- Authlib 版本、许可、维护状态与协议覆盖：<https://pypi.org/project/Authlib/1.6.10/>、<https://python.libhunt.com/authlib-changelog/1.7.0>、<https://skillfed.io/packages/authlib>（均于 2026-09-13 取证）
- 119/119 与 96.6% 两项数据转引自 <https://niteagent.com/blog/mcp-oauth21-resource-server-field-guide-2026>（原始研究 arXiv:2605.22333，未直接核对原文）
