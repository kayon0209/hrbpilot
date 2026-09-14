# 交付包：外部 AI Agent MCP 接入（方案 §13）

> 交付日期：2026-09-14
> 交付方：实施 Agent
> 验收方：Codex
> **本包按方案 §13 的 11 项逐条提供。第 7 项无法提供，原因见该节 —— 它没有被跳过，是没有条件做。**

---

## 1. 仓库、分支与 SHA

| 项 | 值 |
| --- | --- |
| 仓库 | `https://github.com/kayon0209/hrbpilot.git` |
| 分支 | `codex/external-assistant-mcp` |
| base SHA | `868c6aecb80d436f5faeb2858d46a0cda018329c` |
| HEAD SHA | `b012eb68e3ed05614ded6a0555d6c65a290157cc` |
| **是否已推送** | **否**。按方案 §10"不得自行直接推送"，全部提交留在本地分支，由验收方决定推送与合并。 |
| 说明 | 上表 HEAD SHA 是**交付内容**的终态。本文档自身的提交紧随其后（只更新本页的 SHA 与统计，不含代码变更），因此 `git rev-parse HEAD` 会比它多一个提交 —— 验收时以本文档所在提交为准即可。 |

## 2. 提交清单（按工作包）

`git log --oneline <base>..HEAD` 共 24 个提交。按方案 §10 的建议序列对应如下：

| 工作包 | 提交 | 目的 |
| --- | --- | --- |
| WP0 | `e64fa6b` | 记录架构决策、威胁模型与基线。**未选定可信 OAuth 实现前不得进入 WP2** —— 本提交即那个前提。 |
| WP1 | `3595699` `d42e771` `a6ab7b9` | 令牌校验收敛为单一入口；引入 `McpPrincipal` 与唯一授权判定；修掉"两条 MCP 出口结论相反"的越权。 |
| WP2-a | `573464c` `21a49fb` `b47b568` `a1e31e3` | 引入 `public_base_url`；实现 RFC 9728 发现面，`/mcp` 未认证改为 401 挑战。 |
| WP2-b | `c6e9bd8` `47dc664` `8a21ded` `4d9f37f` `55e47fa` | 配置面；自建授权服务器；RS 侧 `iss` 分流校验；44 条测试；端到端脚本 + ADR/威胁模型。 |
| WP2 收尾 | `1f6eeb2` | 协议验收第 6 条：scope 不足返回 403。 |
| WP3 | `c3503a1` `ee43171` `9100dd5` | 分维度限流；审计持久化；管理端撤销 API。 |
| WP4 | `bab3dfe` | `get_my_access_profile` 与响应预算。 |
| WP7 | `dc60ab1` | 案件上下文与审批闭环、审批绑定。 |
| WP5 | `e3015bd` | WorkBuddy 连接器包与私有协议回调支持。 |
| WP8 / §11 | `af9d305` | 回滚开关、紧急撤销脚本、运维手册与兼容矩阵。 |
| ADR 补充 | 本提交 | ADR §14 记录 WP3—WP8；修正 §13.6 中已过时的"未做"说明。 |

## 3. 变更规模

```
git diff --stat 868c6aecb80d436f5faeb2858d46a0cda018329c...HEAD
→ 99 files changed, 13482 insertions(+), 290 deletions(-)
→ 新增文件 71 个，修改文件 25 个
```

完整清单：`git diff --name-status <base>...HEAD`。

## 4. 数据库迁移

| 迁移 | 内容 |
| --- | --- |
| `038_oauth_authorization_server` | `oauth_clients` / `oauth_authorization_codes` / `oauth_tokens` / `oauth_revoked_tokens`（**刻意不上 RLS**） |
| `039_mcp_call_audit` | `mcp_call_audits`（**启用并强制 RLS**） |
| `040_approval_requester_binding` | `approval_requests` 新增 `requester_user_id` / `client_id` / `installation_id` + 索引 |

**实测结果**（对独立 scratch 库 `hrbp_oauth_e2e` 执行，未触碰开发库）：

```
$ alembic upgrade head      → 037 → 038 → 039 → 040，退出码 0
$ alembic downgrade -1      → 040 → 039，退出码 0
$ alembic upgrade head      → 039 → 040，退出码 0
```

三个迁移都提供 `downgrade`，且已实测往返。

**另需说明**：开发库 `hrbp_workbench` 也已执行到 `head`（部分集成测试连真实库）。这是
CI 的既有流程（`.github/workflows/ci.yml` 先 `alembic upgrade head` 再 `pytest`）。

## 5. 测试命令与结果

```
$ .venv/bin/ruff check app tests evaluation      → All checks passed!  (exit 0)
$ .venv/bin/ruff format --check app tests        → 393 files already formatted (exit 0)
$ .venv/bin/mypy app                             → Success: no issues found in 270 source files (exit 0)
$ .venv/bin/python -m pytest -q                  → 760 passed, 50 skipped (exit 0)
```

**无失败、无跳过原因异常的用例。** 50 个 skip 是既有的（依赖外部服务或环境）。

端到端（真实 uvicorn 进程 + 真实 Postgres + MCP 官方 SDK 客户端）：

```
$ python scripts/verify_oauth_end_to_end.py --database-url postgresql+asyncpg://.../hrbp_oauth_e2e
→ 58/58 项通过，退出码 0
```

**与 WP2-b 收尾时的差异**：当时是 55/56（1 项已知缺口）。现在缺口已补齐，且新增了
DCR 与 scope 403 的断言。脚本退出码语义：只有全部通过才返回 0 —— 已知缺口也计入失败，
因为"会返回 0 的验收脚本"本身就是"看起来做了"的来源。

## 6. 脱敏后的协议证据

全部证据可由 `python scripts/verify_oauth_end_to_end.py` 复现。脚本在**出口**统一脱敏
（`Report` 的四个方法与异常构造），三条正则分别覆盖完整 JSON 字段值、被 `text[:200]`
截断后失去闭合引号的值、以及裸 JWT。实测输出中 `eyJ` 出现次数为 **0**。

```
401 挑战        Bearer resource_metadata="<host>/.well-known/oauth-protected-resource"
AS issuer       与 RS 信任列表逐字节一致
JWKS            可读、非空、不含私钥分量 d、令牌头部的 kid 可在其中找到
令牌            iss = AS issuer；带 family_id（撤销粒度）
403 挑战        Bearer error="insufficient_scope", scope="hrb:policy:read", resource_metadata="…"
429             带 Retry-After: 60
```

## 7. 三客户端真实认证 —— **无法提供**

方案 §WP6 要求 WorkBuddy / Codex / Claude Code 的**真实客户端**逐一验证（实际版本、
配置、成功截图/日志、已知差异）。**本轮不具备条件**：需要真实账号与公网可达部署。

**没有用推断代替实测。** 兼容矩阵（`docs/ops/2026-09-14-client-compatibility-matrix.md`）
大面积标注"未实测"，并给出每一格的具体风险与验证方法，以及"已完成的验证"与"它证明了
什么 / 没有证明什么"。

已完成的验证是：**服务端正确 + 一个遵守规范的标准客户端（MCP 官方 SDK）能走通完整链路**。
它不证明任何一个具体客户端能连上 —— 而差异恰恰发生在各客户端的实现取舍上。

**顺带发现并处理了两处会直接阻断 WorkBuddy 接入的差异**（若不做实测前的规范核对，
它们会留到联调时才暴露）：

1. **DCR 是必需的**：WorkBuddy 内置 OAuth 管理器只走 RFC 7591，而我们的 DCR 默认关闭。
   已写进连接器 README 的必需开关表。
2. **私有协议回调**：WorkBuddy 优先用 `workbuddy://…`，而原实现明确拒绝自定义 scheme。
   新增 `OAUTH_CUSTOM_REDIRECT_SCHEMES` 显式清单（默认仍为空），放宽的边界由 8 条测试
   守住（默认拒绝 / 名单外拒绝 / 缺 host 拒绝 / 带 fragment 拒绝 / loopback 的端口放宽
   不延伸到自定义 scheme）。

## 8. 负例结果

端到端与单测覆盖的负例（全部为"稳定拒绝"，无 flaky）：

| 类别 | 负例 | 结果 |
| --- | --- | --- |
| 令牌 | 错误 audience / 陌生密钥 / `alg` 混淆 / `typ` 不符 / 缺声明 / 非信任 issuer / 元数据无 `jwks_uri` / JWKS 无该 `kid` / 文档不可达 | 全部拒绝 |
| 令牌 | 已撤销（`jti` 与 `family` 两个粒度） | 拒绝，且原因码为 `revoked`（与"令牌损坏"分开） |
| 授权码 | 二次兑换 / PKCE 非 S256 / 缺 verifier / 错误 verifier / redirect URI 不完全一致 | 全部拒绝 |
| 刷新 | 重放已用过的 refresh token | 拒绝，且整条 family 失效 |
| 授权 | 跨租户案件读取 / 越权案件读取 | 与"不存在"不可区分 |
| 审批 | 已撤销安装实例的待审批 / 改参数后复用旧审批 / 跨案件读审批 | 全部拒绝 |
| 传输层 | scope 不足 | 403 + `insufficient_scope`（不是 200 信封） |

## 9. 新增依赖

**零新增依赖。**

这是与 ADR-0002 §6 的一处**偏差**：该决策原定"用 Authlib 实现 AS"，实际未引入 ——
`pyproject.toml` 的依赖清单与基线逐行一致。理由与代价见 ADR §13.2（核心是：真正需要
协议逻辑的部分在 Authlib 里都要按它的框架接线，而签名与验签已被基线里的
`python-jose[cryptography]` 覆盖）。

**代价已接受并记录**：协议正确性由本项目自己承担，缓解措施是"每条协议点都配负例"
（见 §8）。

## 10. 已知限制、暂缓项与回滚

**已知限制**（逐条写在 ADR §14.8 与运维手册里，不在代码注释里假装已解决）：

- 无独立指标端点，告警完全依赖日志采集；
- 无异常 DCR 的自动阻断（只有限速）；
- 密钥轮换有实现与单测，**未在真实进程演练**；
- CIMD 无端到端验证（需公网 HTTPS 文档服务器）—— 而它是**主**注册路径，验证程度
  低于兼容回退路径；补它的优先级高于再测一遍 DCR；
- 登录租户固定为 `"default"`（既有行为，多租户部署要先解决）；
- 反向代理后 `request.client.host` 是代理地址，不做可信代理配置时限流形同虚设。

**灰度**：不适用 —— 当前没有生产部署。部署时的灰度顺序写进了运维手册 §2.3
（代理层就把 `/mcp` 与 `/api/*` 分开，这样"关闭外部接入"可以只在代理层做）。

**回滚步骤**（顺序不可颠倒）：

1. `MCP_EXTERNAL_ENABLED=false` 并重启 RS —— `/mcp` 立即 503，工作台不受影响；
2. `POST /api/admin/mcp/revoke-all`（租户级）或
   `python scripts/revoke_all_mcp_installations.py --apply`（跨租户，默认 dry-run）；
3. 必要时停 AS。**注意**：RS 用本地 JWKS 验签，停 AS 不会让已签发令牌立刻失效 ——
   第 2 步才是那个动作。

反过来的话（先撤销、后关开关），新旧进程交替期间新连接仍会被接受。

## 11. 凭据与敏感数据声明

**提交中、日志中、示例配置中均不存在真实 token、secret、生产地址或个人敏感数据。**

- 端到端脚本的输出与日志经出口脱敏，实测 `eyJ` 出现 0 次；
- 连接器包有测试扫描 `eyJ…` / `sk-…` / `Bearer <长串>` 三类形态；
- `mcp.json` 用的是占位域名 `https://hrbpilot.example.com/mcp`，README 把它列为
  **提交前必须替换的第一项**；
- 生产签名密钥在测试里是**导入时生成**而非硬编码 —— 硬编码密钥进仓库是最难清理的一类泄漏；
- 审计表只存参数摘要哈希，不存参数原文。

---

## 附：验收时建议优先看的四处

1. **`app/mcp/transport_guard.py`** —— 403 的实现。重点看它**为什么不是**第二个授权判定点。
2. **`docs/security/mcp-threat-model.md`** 的 T-11 / T-12 —— 两个新增的匿名面。
3. **`docs/ops/2026-09-14-client-compatibility-matrix.md`** —— 不要跳过这页。
4. **`git log <base>..HEAD` 的提交信息** —— 每条都记了"为什么这样做"以及被否决的方案。
