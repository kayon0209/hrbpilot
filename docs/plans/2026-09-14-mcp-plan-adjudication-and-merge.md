# MCP 路线：四份文档的对账、分歧裁定与合并结论

- 日期：2026-09-14
- 状态：**对账备忘录**（不是新调研）。目的：把同一天产出的四份文档收敛成"一份计划 + 一份附件"
- 参与文档
  - **A** `2026-09-14-github-product-hunt-mcp-research.md`（Codex，23:54，471 行）
  - **B** `2026-09-14-natural-language-mcp-task-roadmap.md`（Codex，23:42）
  - **C** `2026-09-14-mcp-direction-research.md`（本人，23:34）
  - **D** `2026-09-14-mcp-natural-language-invocation-research.md`（本人，23:45）
- 核实方式：读四份文档 + 核对仓库代码 + 核对 A 的四处代码指控 + 核对新提交 `9d4375d`

---

## 0. 一句话结论

**A/B 的路线与实施顺序更实，应当采纳为计划；C/D 的技术事实与证据更全，应当作为附件保留——A 第 7 行"两者仅保留为背景材料"的处置应当更正，改为"C/D 降为事实附件"而不是"背景材料"。**
因为如果只留 A，团队会丢掉三件会影响设计正确性的事实：本项目 SDK 已经是最新协议世代、规范已废弃 sampling/roots/logging、以及高层的 `MCPServer` 表层根本不支持 Tasks。A 的第 3.1 节把 Tasks 说成"2025-11-25 仍标记为实验性"，这个基线已经过期。

---

## 1. 采纳 / 保留 / 更正

| 处置 | 内容 |
| --- | --- |
| **采纳为计划（A/B）** | 产品定义改为"**Intent-to-Task Gateway**"：把自然语言编译成可审阅的 HR 任务草稿 → 补信息 → 确认 → 审批 → 异步执行 → 唯一可信任务状态。三层交付（HR 用户 5–7 个任务工具 / 高级端点原子工具 / 管理员治理面）。P0 先修真实链路，P1 做任务受理 MVP，Tasks 与 MCP Apps 延后为适配层。 |
| **保留为附件（C/D）** | 协议世代与能力边界事实、上下文经济数据与阈值、安全事件证据、非技术用户的产品形态案例（Product Hunt 业务侧）。 |
| **更正 1** | Tasks 的基线：项目 SDK `mcp 2.2.0` 的 `LATEST_PROTOCOL_VERSION = 2026-07-28`，**Tasks 已是稳定扩展**（`tasks/get`·`update`·`cancel`），旧的实验性 `tasks/list` 已移除。**"仍实验性"不再成立**；但"暂缓适配"的结论**依然正确，只是理由要换**（见 §3.1）。 |
| **更正 2** | 补充：`sampling` / `roots` / `logging` **已被 2026-07-28 规范废弃**（12 个月窗口）。任何"服务端反向调模型"的设计不要再投。四份文档都没有提这一条。 |
| **更正 3** | A 与 B 互相不一致（P0 2–4 天 vs 1–2 天；P1 1–2 周 vs 3–5 天；P2 1 周 vs 1–2 周）。四份文档应收敛成 **1 份计划 + 1 份附件**。 |

---

## 2. C/D 应当吸收的六处（Codex 强的）

1. **任务型工具的具体契约。** A §6.2 给了 7 个工具的完整清单与副作用标注；§6.3 给了 `prepare_hr_action` 的返回结构（`draft_id` / `status` / `interpreted_goal` / `subject_candidates` / `missing_fields` / `planned_steps` / `risk{level,reasons}` / `required_scopes` / `expires_at` / `confirmation_summary`）。C/D 只到"应该有任务型工具"的层级。**A 可直接开工。**

2. **草稿在服务端冻结 + 规范化参数哈希。** A 明确"不能让客户端在确认后偷偷改参数"。这比 C/D 的"返回草稿计划让用户确认"强一个档次——**确认的对象是服务端的冻结态，不是客户端的话**。而且它复用了项目已有的"参数哈希一致"机制（`begin_tool_execution` 四重检查），零新概念。

3. **任务状态投影，而不是合并内部表。** A §6.4 / B §5 明确"**不要**立刻合并 `work_tasks` / `async_tasks` / `approval_requests`，先做统一投影"，并给出状态机 `draft → input_required → ready_for_confirmation → awaiting_approval → queued → running → terminal`。这是正确的增量设计，C/D 完全没有这一层。

4. **轮询优先的"最小兼容协议"，Tasks 只做适配层。** A §5.8 的对策：`HTTP + OAuth + tools + 轮询 task tools` 为基线，增强能力按客户端协商启用。**这一条直接修正了 D 的一个弱点**：D 把 prompts 放进 P0，却没有为"宿主对 prompts/resources/Tasks/Elicitation/Apps 支持不一致"给出回退方案。A 的 `provide_task_input` / `missing_fields` 就是 elicitation 的兼容回退。

5. **连接成功 ≠ 业务成功，且要变成产品功能。** B §1 要求连接向导结束时**自动执行三项 smoke test**（身份、一个业务只读工具、effective scopes 与预期一致）。这比 D 的"补一条验收说明"强——它是**产品里的一步**，不是文档里的一句话。

6. **Skill/Recipe 作为"这项工作该怎么做"的载体，以及单一 capability manifest。** A §3.5 / B §2：Skill 要包含任务路由、追问顺序、写操作语义、结果解释；并且**从同一份 manifest 生成/校验 WorkBuddy 与 Codex 两个包，不复制权限规则**。D 只有"prompts + 示例说法"，A 的框架更完整（因为 prompts 的宿主支持不确定，而 Skill 随包安装）。

**另外三处 A 有、C/D 没有的工程细节，都值得收**：`idempotency key` + 任务状态与连接/会话解耦（A §5.7）；token passthrough / confused deputy 的具体设计规则（A §5.6：HRBPilot 的令牌只用于访问 HRBPilot）；多副本下 SSE / Streamable HTTP 的粘性路由坑（A §3.6，来自 n8n）。

---

## 3. A/B 应当吸收的六处（C/D 强的）

### 3.1 协议世代与能力边界（**最需要补，且影响 B 的理由**）
- 项目 SDK 已是 **`mcp 2.2.0` / `LATEST_PROTOCOL_VERSION = 2026-07-28`**；`stateless_http=True` 已开。
- 高层 `MCPServer` 表层**不支持** `tasks` / `discover` / `elicitation`（源码 0 命中），MCP Apps 符号只在类型层。**"暂缓 Tasks"的真正理由是这一条**——要做就得下沉到低层 `Server`，成本不是"接一下"。
- `sampling` / `roots` / `logging` 已废弃。
> 建议：把 B §6 的"仍标记为实验性"改为"**项目所用 SDK 的高层表层不提供 Tasks；要做需下沉到低层接口，且客户端特性开关未启用**"。结论不变，理由变硬。

### 3.2 结果侧的大小预算（A/B 完全缺失）
四份文档都在关心"工具数不要太多"，但**更贵的是工具返回结果**。D 有、A/B 没有：
- `response_format: concise/detailed`（实测 206 → 72 token）；
- 默认分页 20 条 + cursor，永不返回 4000 行；
- 单条结果 25,000 token 的截断惯例，**且截断要带引导语**；
- 返回人类可读名称而非 UUID。
> 建议：并入 A §7 的"调整"清单，与 annotations / output schema 同级（都是 P0）。

### 3.3 确认分层的**度量目标**
A §6.5 的"只读无需确认 / 中风险一次确认 / 高风险加强"是**定性**的；D 补上**定量**：确认疲劳是已归类威胁（Rippling **T10**），SOC 类比数据是"日均 4,484 条告警、**67% 因疲劳被忽略**"，业界目标**升级率 10–15%**。
> 建议：把"升级率"写进 B §验收指标——否则"少确认"和"漏确认"无法区分。

### 3.4 安全事件的证据面
A 给的是**规则**（provenance 标记、lockdown 不是边界、禁止 passthrough），这很好；但缺**为什么这些规则值得付出成本**的证据：Invariant Labs 工具投毒（add 工具偷 SSH 私钥）与工具影子、Postmark MCP rug pull（更新后 BCC 外发）、CVE-2025-49596（CVSS 9.4）、CVE-2025-59536（Claude Code `settings.json` hook 在信任对话框之前执行 + 静默自动批准 MCP）、7,000+ server 中 36.7% SSRF、492 个零认证。
> 建议：作为 A §5.5 的脚注保留（并保留"来自安全厂商博客、未独立核验"的标注）。

### 3.5 面向**业务用户**的 Product Hunt 案例（A 的 PH 选择偏基础设施）
A 的 PH 案例是 mcp-use / Pipedream / Composio / Strata / FastMCP / n8n / goose —— 全是**开发者工具**。对一个"给 HR 经理用"的题目，D 侧的 PH 选择更对靶：**Accordio**（把强制人工检查点当合规卖点）、**Well**（55+ 实体本体 + 为没有 MCP 的工具自动生成 MCP）、**Salestrics**（**斜杠命令** + 管理员 token 预算 + 按应用权限）、**Apideck**（360 工具 → 4 个 meta-tool）、**Peach**（托管零代码；评论区最热的问题是"数据流向哪、留多久"）。
> 建议：A 的"三层交付"里补上**斜杠命令**这一项（非技术用户的主入口），以及**管理员 token 预算 / 按应用权限**（A 目前只有 bundle 上限与 trace）。

### 3.6 上线与培训清单
D 有：预置 bundle、**2–3 个示例提示词**、30 分钟培训、团队 champion、**说明"AI 记得什么、谁能看、怎么纠正"**、以及面向安全团队的**数据流向与留存说明**。
> A §6.1 只有"暴露什么"，没有"怎么让第一批人用起来"。建议并入 P2。

---

## 4. 三处分歧的裁定

### 分歧 1：项目内聊天窗口是主路线还是可选项？
- C 主张**主推**（理由：本机浏览器没有沙箱障碍，能把"授权→调用→审批"整条链路演示完整）。
- A/B 主张**降为可选**（理由：Pipedream 的开源结构证明内置聊天需要模型选择、消息持久化、登录、会话留存、流式 UI、错误恢复，会显著扩大产品面，**却不修复当前缺失的任务闭环**）。

**裁定：A/B 对，C 应当让步。**
C 的理由（可演示、无沙箱障碍）是**演示价值**，A/B 的理由是**产品价值**。而且 B 的实测数据给了决定性证据：当前真正的障碍是 **scope 过窄（11 个工具只有 1 个可用）+ WorkBuddy 尚未认证 + 缺任务受理层**——这些都不是"缺一个聊天框"。
**保留的部分**：项目内窗口仍值得做一个**只读的兜底/演示入口**（给没有 Agent 的用户、也给验收用），但排在 P2 之后，不进主路线。

### 分歧 2：Prompts 该不该进 P0？
- D：进 P0（零风险、装饰器级别、把"该怎么问"搬进协议）。
- A/B：把 annotations / output schema / 可纠错错误 / 版本化描述 + **评测**提前，protocol 特性按宿主支持协商。

**裁定：两者都对，但要分清角色。**
- prompts 作为**附加能力**可以进 P0：宿主不支持时它只是不可见，**不产生坏结果**。
- 但**不能把"怎么做事"只押在 prompts 上**——那部分归 **Skill**（A 的主张），因为 Skill 随包安装、可版本化、两端一致。
> 落地：prompts 进 P0 作为"低成本附加"；业务路由与追问顺序写进 Skill；不重复。

### 分歧 3：工具数阈值——用固定数字还是让评测决定？
- D：给了锚点（~10 个开始退化、>15 明显变差、>50 上 meta-tool）。
- A：不预设行业通用数字，达到评测阈值再引入搜索。

**裁定：两者互补，都要。**
A 的"让评测决定"是**治理原则**（正确，防止拍脑袋）；D 的数字是**规划锚点**（让你知道什么时候该开始准备，而不是等到发现退化）。
> 落地写法："以评测为准；经验锚点是 ~10 个工具后开始观察、>15 明显变差、>50 引入渐进式发现。**在 11 个工具规模下不做 meta-tool/Code Mode**。"（A 的"现在不做"结论保持不变。）

---

## 5. 已核实的 Codex 代码指控（全部成立）

| A/B 的指控 | 核实结果 |
| --- | --- |
| 前端仍展示已移除的 `hrbpilot_ping` | ✅ 成立。`app/` 中已无此工具，但 `web/src/features/mcp/toolCopy.ts:41` 与 `McpPage.tsx:35` 仍有 |
| 前端写"查询类允许匿名试用"，与服务端不一致 | ✅ 成立。`McpPage.tsx:139`；服务端业务读取实际要求登录，匿名只拿到 `AUTH_REQUIRED` 信封 |
| 工具没有 `ToolAnnotations`、没有准确 output schema | ✅ 成立。`server.py` 与 `tools.py` 中 `annotations`/`readOnlyHint`/`outputSchema`/`structuredContent` 均无命中 |
| WorkBuddy 包是待发布状态（URL/图标占位） | ✅ 成立。`connectors/workbuddy/mcp.json` 为 `https://hrbpilot.example.com/mcp` |
| 接入脚本未显式请求 scope → 只拿到 `hrb:profile:read` | ✅ 成立。`scripts/connect-codex-local-mcp.sh` 中无 scope 参数 |

**一处 Codex 略微低估**：`connectors/workbuddy/connector-meta.json` **已经是一份可用的成品**——双语名称与描述、5 条中文示例话术（"公司的年假是怎么规定的？"等）、`minWorkbuddyVersion`。所以 P2 的"发布 WorkBuddy 包"**不是从零开始**，缺的是真实的 MCP URL、图标与提审。

---

## 6. 合并后的 P0（建议按此顺序，约 3–4 天）

**前置：先修真实链路（A/B 的 P0，优先级最高，C/D 缺这一层）**
1. 接入脚本显式请求 scope 套餐（查询版 4 项；办理版加 `hrb:case:propose`），结束时校验 **effective scopes**；旧窄 scope 凭据提示重新授权。
2. WorkBuddy 侧完成认证直到 CLI 不再显示 `Needs authentication`。
3. 连接向导加三项 smoke test（身份 / 一个业务只读工具 / scope 套餐一致）。

**并列：把"模型会不会用对"变成可测（C/D 的主张，A/B 支持但排序稍后）**
4. 建 50–100 条中文 HR 口语黄金集（正确工具 / 必要参数 / 应拒绝 / 应补问 / 最终状态），含 held-out。
5. 为 11 个工具补 `ToolAnnotations`、output schema、**可自纠错错误**（`fix` + `retryable`）、版本化中文描述（含"何时不用"+ 1–2 个真实例子）。

**并列：结果侧预算（D 独有，A/B 缺）**
6. 分页默认 20 + cursor、结果上限与截断引导、`response_format: concise/detailed`、返回人类可读名称。

**清理**
7. 修 `hrbpilot_ping` 与"匿名试用"两处前端漂移；补 prompts（低成本附加）；Skil​l 的业务指令先落到 `connectors/workbuddy/skills/`。

**完成门槛**（沿用 A 的判据，我建议再加一条）
- WorkBuddy 与 Codex 都能从自然语言完成"查制度"；Codex 完成一次"提交跟进任务审批 → 查询真实状态"；未经批准写入 = 0。
- **再加**：黄金集跑出**首次基线数字**（当前完全没有基线，无法判断后续改动是否变好）。

---

## 7. 建议的文档整理动作

1. **把 A 的路线部分 + B 的验收数据 → 合并为唯一计划文档**（建议命名 `2026-09-14-mcp-intent-to-task-plan.md`），修掉 §1 更正 1/2 与两份文档之间的工期不一致。
2. **C/D 合并为一份附件**（建议命名 `2026-09-14-mcp-technical-facts-and-cases.md`），只保留事实、数据与证据，删掉与 A 重复的路线建议。
3. **A 第 7 行的"取代"改为**："路线结论由 B 与本计划取代；C/D 转为事实与证据附件（含协议世代、废弃项、上下文数据、安全事件）"。
4. 四份文档里的"厂商自述数字"统一标注证据强度——A §11 的自我约束（不把用户数/准确率/token 节省当架构结论）是**比我更严格的做法，值得沿用**；我在 D 里把 Anthropic 的 72%→90% 等标为"可验证的一手来源"其实偏宽松，应改为"厂商公开的工程结论，非独立复现"。

---

## 附：本备忘录的两个结论性判断

**判断一：这次是一个健康的双轨结果。** A/B 赢在"接地"（真实客户端验收数据、可实施的契约、明确的验收门槛），C/D 赢在"地基"（协议世代、能力边界、量化锚点、安全证据）。**没有哪一份应当作废**，但**必须先合并再执行**——否则 P0 会按 A 走，而 P0 里"补 annotations / output schema / 错误可纠错"的理由要回到 D/E 才有依据；而"暂缓 Tasks"如果继续用"仍实验性"当理由，下一次评审会被一条过期事实推翻。

**判断二：A/B 里最有价值的其实不是调研，是那 6 行真实客户端验收表。** 它第一次把"点了授权还是不能用"拆成了三层（scope 过窄 / 客户端自身工具确认 / 缺任务受理层），并给出了"11 个工具只有 1 个可用"这种可执行的事实。这个数据比任何二手案例都有用，**应当直接进 P0 的验收基线**。
