# MCP 发展方向调研（讨论稿）

- 日期：2026-09-14
- 状态：**调研/讨论稿**，未形成决策，不作为实施依据
- 关联：`ADR-0001`（单有界 Agent）、`ADR-0002`（MCP RS/AS）、`2026-09-13-external-assistant-mcp-execution-plan.md`
- 方法：读本仓库代码 + 官方规范/博客 + 生态实践资料；凡未实测的一律标注

---

## 0. 结论先行

### 0.1 一句话判断

本项目**已经完成了"对外做 MCP 服务端"这一步，但还没有"对内做 Agent 宿主"这一步**。
你现在想要的东西（在项目自己的窗口里用自然语言干活、同时外部 Agent 也能调用项目）不是两条产品线，而是**同一个工具面上的第三种出口**。

### 0.2 三个最重要的发现

**发现一：你手里最值钱的资产不是工具数量，而是那条唯一的判定链。**
`authorize_tool_call`（角色能力 ∩ scope ∩ 客户端授权上限）已经被 `/mcp` 与 `/api/mcp` 两条出口共用，写操作统一收敛成 `ApprovalRequest`。这意味着**再加一条出口（产品内的自然语言窗口）不需要新造能力**——只需要新造"入口 + 会话 + 展示"。这是低成本高确定性的路径。

**发现二：生态已经把"工具面"变成商品，差异化全部落在治理侧。**
2026 年 MCP 已是被 OpenAI/Google/Microsoft 全采纳的"AI 时代 USB-C"（月下载近亿级），公开 server 上万。规范明确写着"厂商在治理、审计、连接器市场、运营工具上做差异化，而不是在 wire format 上"。本项目重仓的恰好是**审批门 + 审计 + 撤销闭环**——也就是该加码的那一侧。**不要去拼工具数量。**

**发现三：你下午的两个技术判断，踩在规范的方向上；但有一个能力面被规范"废弃"了，别再投。**
- ✅ CIMD 作为主注册路径 —— 规范 2026-07-28 正式"从 DCR 转向 CIMD"，DCR 进废弃流程。你走在前面。
- ✅ 无状态核心（`stateless_http=True`）—— 正是新规范的头号变化。
- ⚠️ **`sampling` / `roots` / `logging` 已被废弃**（12 个月窗口）。任何"让服务端反向调用客户端模型"的设计都不要做，方向改成了 MRTR 与 Tasks。

### 0.3 三条方向与推荐顺序

| 方向 | 一句话 | 我的建议 |
| --- | --- | --- |
| **A. 对外深化** | 从"能连上"到"连得稳、连得省、连得合规" | 其中 4 项今天就能做（低成本），2 项需等 SDK |
| **B. 对内引入 Agent 窗口** | 把「在线体验」从填表单升级为会说话的窗口 | **主推**，用户价值最直接、复用既有资产最多 |
| **C. 双向与实时** | 让"提交了待审批"变成"审批完自己回到对话" | **B 的收尾**，不做则 B 是个半成品 |

**推荐节奏：B 为主体，A 的低成本项并行，C 紧随 B。**

---

## 1. 项目现状盘点（以代码为准）

### 1.1 对外服务端（Server 角色）

| 维度 | 现状 |
| --- | --- |
| 传输 | `/mcp`（Streamable HTTP，**`stateless_http=True`**，已挂）+ stdio（本机） |
| 工具面 | **11 个**：5 读（`search_policy`/`get_policy_source`/`search_cases`/`get_case_summary`/`get_approval_status`/`get_my_access_profile`）+ 6 写（全部只创建审批） |
| 资源 | **1 个**：`hrbpilot://capabilities` |
| Prompts | **0 个** |
| 判定 | `authorize_tool_call`，两条出口（`/mcp`、`/api/mcp`）共用；`denial_envelope` 同源 |
| 写路径 | `ApprovalRequest` → 人工审批 → `APPROVED → CONSUMED`；`.mcp` 审计与审批**同事务** |
| 认证 | RS 实时复查（客户端已注册/未禁用/未拉黑、用户 `is_active`/`auth_version`/角色一致），fail-closed |
| 注册 | CIMD（主）+ DCR（兼容）+ 预注册（可声明租户） |
| 撤销 | `revoke_family` 同事务把该 installation 的 `PENDING/APPROVED` 审批置 `EXPIRED` |
| 部署 | `401 + WWW-Authenticate: Bearer resource_metadata=...`（RFC 9728）已在线上实测通过 |
| 管理面 | 前端「AI 助手连接管理」：列客户端/实例、封禁/放行、单实例撤销、紧急全撤销 |

### 1.2 技术世代（**这是最容易被忽略的一节**）

```
mcp SDK          = 2.2.0
LATEST_PROTOCOL  = 2026-07-28   ← 已是最新世代
项目用法         = mcp.server.mcpserver.server.MCPServer（高层表层）
```

`MCPServer` 表层**已提供**：`tool` / `resource` / `resource_template` / `prompt` / `completion` / `icons` / `instructions` / `middleware` / `custom_route` / `stateless`。

`MCPServer` 表层**不提供**（源码 0 命中）：`tasks`、`discover`、`elicitation`。
→ 含义：**Tasks、MRTR/elicitation 要用低层 `Server` 或等 SDK 跟进**，不是"写个装饰器就行"。

MCP Apps 相关符号（`io.modelcontextprotocol/ui`、`_meta.ui`、`resourceUri`、`ui://`）在**类型层存在**，但只在 1 个文件里出现 → **需要手工接线，没有糖**。

> ⚠️ 未实测项：我**没有**验证运行中的 server 实际协商到哪个协议版本（需要带凭据的客户端探测）。上表是 SDK 能力，不是运行态结论。

### 1.3 产品内（Host 角色的雏形）

- LLM 已配置：`llm_provider=deepseek`、`llm_model=deepseek-v4-flash`、`temperature=0.3`、`max_tokens=2048`。
- 已有**有界 Agent**：`hr_case_agent`（1813 行）——LLM 只能产出 `CasePlan`，服务端重校验（白名单/风险策略），`MAX_PLAN_STEPS=5`、`MAX_STEPS_PER_RUN=8`，模型输出**永不直接触库**。
- `ADR-0001` 已否决 multi-agent，理由是可审计性、审批边界、评测可重复——**这个决定现在仍然正确**（见 §4.2）。
- 前端 MCP 页（`web/src/features/mcp/McpPage.tsx`，540 行）：
  - 说明区（这是什么 / 能帮你做什么 / 两类工具怎么选 / 三步）
  - 管理员「AI 助手连接管理」
  - **「在线体验（不装 AI 助手也能试）」← 这就是你说的"MCP 功能窗口"，当前是"按 schema 生成中文表单 → 填参数 → 调用"**

### 1.4 项目自己记录的未做项

`WP6`（真实客户端认证）、独立指标端点、异常 DCR 自动阻断、可信代理限流（`X-Forwarded-Host` 不推导）。

---

## 2. 生态现状（2026-09）

### 2.1 规范：2026-07-28 已正式发布（**破坏性**）

| 变化 | 内容 | 对本项目的含义 |
| --- | --- | --- |
| **无状态核心** | 移除 `initialize` 握手与 `Mcp-Session-Id`；协议版本/客户端信息走每请求 `_meta`；新增 `server/discover` | ✅ 已符合（`stateless_http=True`）；水平扩容不再需要粘性会话 |
| **头路由** | `Mcp-Method` / `Mcp-Name` 进 HTTP 头，网关可按头路由与授权 | 🔸 潜在收益：可以在网关层做更细的限流/审计，不必解析 body |
| **列表可缓存** | `tools/list` 带 `ttlMs` / `cacheScope`，确定性顺序 | 🔸 今天就能用，客户端缓存可省往返 |
| **MRTR** | `input_required` 结果 + `inputResponses` 重放，**取代** sampling / elicitation / roots | 🔸 这正是"写操作要用户中途确认"的标准做法 |
| **Extensions 框架** | 扩展有反向域名 id、独立仓库/版本/维护者 | 🔸 能力接入方式规范化 |
| **授权硬化** | RFC 9207 issuer 校验、OIDC/OAuth 发现双支持、**DCR 转废弃 → CIMD** | ✅ CIMD 已是主路径 |
| **工具 schema** | 强制 JSON Schema 2020-12 | 🔸 需核对现有 `input_schema` |
| **废弃政策** | 废弃项至少保留 12 个月 | ✅ 升级有缓冲，不必抢跑 |

**已废弃（别再投）**：`roots`、`sampling`、`logging`。

### 2.2 三个扩展（都已 stable）

| 扩展 | stable 时间 | 是什么 | 对项目的适配度 |
| --- | --- | --- | --- |
| **MCP Apps** | 2026-01-26 | 服务端声明 UI 资源（`_meta.ui.resourceUri` → `ui://`），宿主在**沙箱 iframe** 里渲染；UI 内动作走**同一条 JSON-RPC 同意与审计路径** | ⭐ 高——审批卡片、案件表单、结果表格可以直接渲染进 Codex/WorkBuddy/Claude，而不是让模型念 JSON |
| **Tasks** | 2026-07-28 纳入扩展框架 | 长任务：`taskId` + `tasks/get`/`update`/`cancel`，`input_required` 可中途要人确认；**句柄可跨连接断开存活** | ⭐ 高——"等 HR 审批"天然是长任务 |
| **EMA**（企业托管授权） | 2026-06-18 | IdP 集中授权（ID-JAG / Okta XAA），员工**登录即得**连接，无逐应用同意；目前 Entra/Google 在 roadmap | ⭐⭐ 对本项目**战略相关**——HR 系统的采购方就是安全团队，"逐人授权"正是企业落地的最大阻力 |

**生态现状的硬事实**：MCP 由 Linux Foundation 的 AAIF 治理（146+ 成员，含 Anthropic/Google/OpenAI/Microsoft/AWS）；2026 年披露过大量**零认证**的公开 MCP 实例（Trend Micro 找到 492 个）与 20 万级潜在脆弱实例；NSA/DoD 于 2026-06-02 发布 MCP 安全指南。

### 2.3 Roadmap 四个优先域（下一个规范周期）

1. **Agentic Messaging Primitives**：Tasks / `subscriptions/listen` / Triggers & Events / **webhooks** —— 让服务端能主动告诉客户端"活干完了"，而不是让客户端轮询。
2. **HTTP-native Transport 统一与硬化**：HTTP over stdio（单一传输模型）、**ETag 缓存**、跨面统一错误处理。
3. **Agent 身份与企业安全**：**DPoP**（持有证明）、**agent identity & delegation**（云工作负载自己的身份、子 Agent 拿到比父 Agent 更窄的权限）。
4. 治理成熟。

> 第 3 条值得单独标记：`DPoP` 与"子 Agent 权限收窄"直接对应本项目的两个既有概念——**令牌盗用防护**与**installation 级撤销范围**。这是一个可以提前对齐的点。

### 2.4 A2A：与 MCP 分层，不是竞品

- MCP = Agent ↔ **工具**（垂直，本项目已在做）；A2A = Agent ↔ **Agent**（水平，Google 主导，v1.0，AAIF 治理）。
- 微软的官方建议：**内部流程优先用平台原生编排**，工具/数据访问用 **MCP**，**跨平台/跨组织**的 Agent 协作才用 A2A。
- 生态共识："**绝大多数产品在 2026 年还不需要 A2A**。"
- → 对本项目：**A2A 现在不是方向**。若将来出现"外部 Agent 要委托本项目的 Agent 干活（而不是调工具）"，再评估。

### 2.5 公认痛点：工具过载 → 渐进式发现

当 MCP 主机连了多个 server、累积几百个工具时，**工具定义会在用户还没说话前吃掉大量上下文**。业界做法：

- **阈值切换**：工具定义占上下文窗口 1%–5% 时，切到渐进式发现；
- **Meta-tool 模式**：只暴露 `discover_tools(query)` + `call_tool(name, args)` 两个轻工具，启动开销从十几万 token 降到 ~2000（降幅 98%+）；经验阈值是"工具数 > 50 或单个 schema > 2000 token"；
- **编程式调用**：让 Agent 写代码调 MCP，中间结果留在执行环境，只把结论送回上下文。

→ 对本项目：11 个工具**远未到阈值**，现在做这个属于过度设计。但**要把它写进设计约束**：工具面扩张时必须同步引入渐进式发现，否则工具越多越笨。

---

## 3. 差距矩阵（项目 vs 标准/生态）

| 能力 | 项目现状 | 标准/生态 | 价值 | 成本（当前 SDK） |
| --- | --- | --- | --- | --- |
| 无状态 HTTP | ✅ 已开 | 2026-07-28 核心 | — | — |
| CIMD 注册 | ✅ 主路径 | 规范转向 CIMD | — | — |
| 撤销/审计闭环 | ✅ 同事务 | 生态稀缺 | **高（差异化）** | — |
| Prompts | ❌ 0 个 | 一等原语 | **高**：把"该怎么问"变成协议资产 | **极低**（装饰器） |
| Resources 扩充 | 🔸 1 个 | 一等原语 | **高**：制度/案件可直接以资源暴露 | **极低** |
| 列表缓存 `ttlMs` | ❌ | 规范新增 | 中：省往返 | 低 |
| 工具元数据（title/icons/annotations） | 🔸 部分 | 规范 | 中：客户端展示更清楚 | 低 |
| EMA | ❌ | stable 扩展 | **高（企业采购关键）** | 中（需 IdP + ID-JAG 校验） |
| Tasks（长任务） | ❌ | stable 扩展 | **高**（审批天然长任务） | **高**（MCPServer 不支持，需低层） |
| MRTR/elicitation | ❌ | 取代 sampling | 中高：写操作中途确认 | 高（同上） |
| MCP Apps | ❌ | stable 扩展 | **高**（审批卡片进客户端） | 中高（类型层有、需手工接线） |
| 渐进式工具发现 | ❌ | 最佳实践 | 低（11 工具） | 低（但先记约束） |
| DPoP / agent delegation | ❌ | roadmap 优先域 | 中（提前对齐） | — |
| A2A | ❌ | 另一层协议 | **现在低** | — |

---

## 4. 三条方向详述

### 4.1 方向 A：对外深化（继续做 Server，但做深）

**A1 · Prompts（建议立刻做）**
现在客户端拿到的是 11 个工具 + 一段 description，模型得自己拼"先搜制度还是先查案件"。加上 prompts 之后：

- `先查制度再引用出处`、`受理一个员工诉求的标准流程`、`案件结案前检查清单`；
- 中文模板，直接对齐项目已有的"用一句大白话"产品文案；
- **关键收益**：把"正确的用法"从文档搬进协议，外部 Agent 不需要被人教会；也顺带降低 prompt injection 的发挥空间（有既定流程可依）。

**A2 · Resources 扩充（建议立刻做）**
现在只有一份 capabilities 文档。可加：制度目录树、案件类别/风险等级字典、审批状态机说明、角色能力矩阵。
**关键收益**：Agent 不必靠猜枚举值；`resource_template` 还能支持按 id 取单条。

**A3 · 列表缓存与工具元数据（建议立刻做，顺手）**
`ttlMs`/`cacheScope` + `title`/`icons`/`annotations`（readOnly / destructive 提示）。
**关键收益**：客户端能缓存工具目录、能正确显示"这个是只读的、那个要审批"——正好和前端已有的"查询类/办理类"文案同构。

**A4 · EMA 预研（建议列入近期评估）**
理由：本项目的买家是 HR/IT，而"每个员工都要点一次授权"是企业落地的头号阻力（这是规范官方给出的立项理由）。EMA 让 IdP 集中授权、员工登录即得。
**但是**：Okta 首发，Entra/Google 尚未支持 → 国内企业多半是钉钉/企微/自建 IdP。所以定位应是"**先把授权模块设计成可插入 ID-JAG 校验的形状**"，而不是马上接 Okta。

**A5 · Tasks（等 SDK 或自建）**
"提交审批 → 等 HR 批 → 结果回来"是天然的长任务。当前 `MCPServer` 不支持，要么用低层 `Server`，要么等 SDK。**这一项与 §4.3 强绑定**：没有 Tasks，实时性只能靠客户端轮询。

**A6 · MCP Apps（中期，收益明确但要动手接线）**
把"待审批卡片、案件摘要表、拒绝原因"渲染进客户端沙箱 iframe，而不是让模型念 JSON。
**关键收益**：审批这件事**本来就该看一眼再点**，纯文本是错的形态。且规范明确"UI 内动作走同一条同意与审计路径"——**不会绕开你的审批门**，安全模型不退化。
**风险**：在宿主里渲染服务端 HTML/JS 引入 XSS 类风险（这是规范发布后安全研究者最集中的批评点）；必须先做 CSP/沙箱审查。

**A7 · 渐进式工具发现（记约束，暂不实现）**
见 §2.5。**写进设计约束**即可：工具数超阈值时同步引入 `discover_tools`/`call_tool`。

**A8 · 既有 backlog（指标端点 / DCR 自动阻断 / 可信代理限流）**
属运维成熟度，与本次方向讨论正交，按原优先级推进即可。

### 4.2 方向 B：对内引入 Agent 窗口（**主推**）

**现状 → 目标**

```
现在：MCP 功能窗口 = 按 schema 生成的表单（填参数 → 调用 → 看 JSON）
目标：MCP 功能窗口 = 会说话的窗口（"帮我把王工的离职案件标成已解决" → 提交审批 → 回执）
```

**为什么这件事性价比最高**

1. **能力已存在**：`TOOL_CATALOG` + `authorize_tool_call` + `ApprovalRequest` 已经是全系统唯一事实源，前端 `toolForm.ts` 甚至明确写着"刻意不在前端另维护一份字段表"。**加一条出口 ≠ 加一套能力。**
2. **风险可控**：写操作仍走审批门，读操作仍走 `resolve_visible_user_ids`（与 REST 同一套可见性）。**"LLM 不能直接触库"这条既有不变式不用破。**
3. **用户价值直接**：不用装任何外部工具就能体验；也是外部客户端的"试驾场"。
4. **是 WP6 的替代验证路径**：真实客户端接入受沙箱/网络约束（已经踩过），而**产品内窗口是本机浏览器，天然没有那个障碍**——它能把"授权 → 调用 → 审批"整条链路演示完整。

**关键设计问题（必须现在回答，否则会返工）**

| 问题 | 建议 |
| --- | --- |
| 会话状态放哪 | 协议核心已无状态、`initialize` 已移除。**建议：服务端签发显式 `conversation_id`，作为普通参数在工具间传递**（规范官方推荐的 explicit-handle 模式），不要藏在连接里 |
| 上下文从哪来 | 只从**已授权可见**的数据里取；检索走既有 read_executors，不新开一条查询路径 |
| 写操作如何确认 | 两道：① 复述确认（对话内）② 既有 `ApprovalRequest` 人工审批（系统内）。**不得因为"用户已经在对话里点过确认"就跳过审批** |
| 是否新增 Agent 循环 | **不新增**。复用 `hr_case_agent` 的有界循环与 `Planner.validate`（ADR-0001 的决定仍然成立）。这是"新的受控 Agent 入口"，不是"新的 multi-agent" |
| 审计怎么写 | 与 MCP 出口**同一张审计表、同一套 outcome codes**，`surface` 标为 `in_app_agent`（既有的 `_SURFACE` 机制就是为此设的） |
| 权限模型 | 与 MCP 出口**完全同一判定**。绝不为"自家窗口"放宽（否则就重演 WP1 修掉的"两条出口结论相反"） |
| LLM 边界 | 沿用 `deepseek-v4-flash`；prompt 与工具返回都要防注入（工具返回内容视为**不可信数据**，不作为指令） |

**三个可选档次（建议按档次递进，不要一次做满）**

| 档次 | 内容 | 风险 | 建议 |
| --- | --- | --- | --- |
| **B1 只读问答** | 自然语言 → 读工具 → 带出处的回答 | 极低 | **先做这个，用来验证链路与体验** |
| **B2 只读 + 写审批** | 增加写工具，产出待审批 + 回执 | 低（审批门挡住） | B1 稳定后立刻做 |
| **B3 流式 + 多轮澄清** | 流式输出、中途澄清（MRTR 思路） | 中 | 与方向 C 一起做 |

**与 A2A 的关系**：无。这是"项目内的 Host"，不是"Agent 之间的协作"。**不要引入 A2A 来解决这个需求。**

### 4.3 方向 C：双向与实时（B 的收尾）

**要解决的真实痛点**：现在用户提交后得到的是一句"已提交，审批编号 X"，然后**没有然后了**。用户必须回到系统里查，或者再问一次。

| 手段 | 说明 | 依赖 |
| --- | --- | --- |
| **C1 审批结果回填** | 审批完成/拒绝后，把结果推回对话（或下次对话时主动提示） | Tasks 或通知机制 |
| **C2 订阅/推送** | `subscriptions/listen`、webhook（roadmap 明确方向） | 规范演进 |
| **C3 跨端一致性** | 同一案件在 REST / 产品内 Agent / 外部 MCP 三处看到的一致 | 已有先例：`resolve_visible_user_ids` 统一了 MCP 与 REST 的 ACL |

**C 的意义**：把"提交了待审批"闭环成"**办完了，这是结果**"。没有 C，B 只是个"更好用的表单"；有了 C，才真正是"**实时协同工作**"——也正是你描述的那个想象。

---

## 5. 推荐路线图

### P0（低成本、高确定性，可立刻做）

1. **Prompts**：3–5 个中文流程模板（先查后引、受理流程、结案检查）。
2. **Resources 扩充**：制度目录、案件类别字典、审批状态机、角色能力矩阵。
3. **列表缓存 + 工具元数据**：`ttlMs`/`cacheScope`、`title`/`icons`/`annotations`。
4. **补验收盲区**：e2e 增加"prompts/resources 可用"断言；**并补一条"浏览器参与"的验收说明**——这次的 CSP 故障正是"脚本全绿、浏览器全废"造成的，必须写进方法论文档。
5. **核对** `input_schema` 是否满足 JSON Schema 2020-12。

### P1（主体工程）

6. **B1 只读自然语言窗口**（产品内，复用既有判定与工具面）。
7. **B2 写 + 审批回执**。
8. **C1 审批结果回填**（让"待审批"闭环）。
9. **EMA 预研**：把授权模块改成可插 ID-JAG 校验的形状（不做实现）。

### P2（条件触发，观察）

10. **Tasks**：等 SDK 支持，或确认长任务确有必要时用低层 `Server` 自建。
11. **MCP Apps**：当"审批需要看内容才能决定"成为实际抱怨时启动。
12. **渐进式工具发现**：工具数接近 50 时启动。
13. **DPoP / agent delegation**：等规范落地；与 installation 级撤销对齐。
14. **A2A**：仅当出现"外部 Agent 委托本项目 Agent 干活"的真实需求时评估。

---

## 6. 风险与明确不做

**明确不做（部分已在原方案否决）**

- ❌ 通用聊天 UI（原执行方案已否决）；
- ❌ 把项目做成 multi-agent（`ADR-0001` 决定仍成立）；
- ❌ 让 LLM 直接触库或直连 HTTP（既有不变式）；
- ❌ 无审批的写操作（既有不变式）；
- ❌ 用浏览器自动化冒充正式协议接入（原方案已否决）；
- ❌ **围绕 `sampling`/`roots`/`logging` 的任何设计**（规范已废弃）；
- ❌ 现在引入 A2A 或渐进式工具发现（未到阈值）。

**风险**

| 风险 | 说明 | 缓解 |
| --- | --- | --- |
| **prompt injection** | 工具返回的制度/案件正文可能含指令；产品内窗口把它喂给 LLM | 工具返回值一律视为**不可信数据**；沿用既有"必须引用出处、不得凭常识补充"约束；写路径仍由审批门兜底 |
| **MCP Apps 的 XSS 面** | 在宿主里渲染服务端 HTML/JS | 沙箱 iframe + 严格 CSP；模板先过安全评审；提供纯文本降级 |
| **"看起来做了"** | **本次已经发生过一次**：e2e 74/74 全绿，浏览器点授权却毫无反应 | 验收判据必须包含"真实浏览器/真实客户端"这一层；文档明确写"脚本绿灯不构成端到端证据" |
| SDK 世代升级成本 | Tasks/Apps/MRTR 需低层接口或等 SDK | 先做 P0 的低成本项；把与扩展相关的代码收敛在少数模块，避免扩散 |
| 本地 AS 用临时签名密钥 | `env.docker` 无 `OAUTH_*`，重启即换密钥 | 生产必须配 `OAUTH_SIGNING_KEY_PEM`（已在运维文档记录） |

---

## 7. 需要你决策的三件事

1. **产品内 Agent 窗口做不做、做哪一档？**（建议：做，从 B1 只读起步。）
   - 它同时替代了 WP6 作为"可演示的完整链路"，因为不依赖沙箱外的真实客户端。
2. **对外深化的边界到哪？**（建议：P0 四项立刻做；EMA 只做预研，因为国内 IdP 支持未定；Tasks/MCP Apps 进 P2。）
3. **A2A 要不要现在就考虑？**（建议：不。生态共识是大多数产品 2026 年不需要，且它会引入与本项目"单有界 Agent"哲学冲突的多 Agent 协商模型。）

---

## 附：本次核对过的技术事实（可复现）

```bash
# 协议世代
.venv/bin/python -c "import mcp_types; print(mcp_types.LATEST_PROTOCOL_VERSION)"
# → 2026-07-28

# 高表层能力边界（tasks/discover/elicitation 命中为 0）
.venv/bin/python -c "import inspect; from mcp.server.mcpserver.server import MCPServer; \
print([m for m in dir(MCPServer) if not m.startswith('_')])"

# 无状态已开启
grep -n "stateless_http" app/main.py
# → app/main.py:159: stateless_http=True

# 未授权访问的挑战式响应（RFC 9728）
curl -sS -D - -o /dev/null -X POST http://localhost:8001/mcp \
  -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
# → 401 + www-authenticate: Bearer resource_metadata="http://localhost:8001/.well-known/oauth-protected-resource"
```

**未实测、需后续补证的项**：运行中的 server 实际协商到的协议版本；MCP Apps 在本 SDK 上的可行接线方式；`tools/list` 的 `ttlMs` 是否已在运行的 server 上生效。
