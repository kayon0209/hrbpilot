# 工具路由基线 —— 首个真实数字

- 日期：2026-09-15（Windows 侧交付脚本）→ **2026-09-16 补跑出真实数字（macOS 本机）**
- 评测器：`scripts/eval_tool_routing.py`（真实 MCP 端点 + 真实令牌 DCR+PKCE + 真实 LLM 路由判断）
- 黄金集：`evaluation/tool_routing_golden.jsonl` —— 56 条（train 48 / held-out 8）
- 分支：`codex/mcp-intent-to-task-v1`（基线 `5cfc800`）
- 运行环境：macOS 本机，venv 起的 `app(8001)` + `oauth-as(8002)` + Docker 起的 postgres/redis/minio/etcd
- 路由模型：`GLM-5.3-Flash`（Gitee AI，OpenAI 兼容接口）
- 明细：`evaluation/results/tool_routing_20260915T180303Z.jsonl`

## 本轮状态：**首个数字已产出，但先读「数字可信度」一节再下结论**

```
== 路由基线 ==
工具路由准确率:      18/37
正确补问率:          7/9
正确拒绝率:          7/7
multi 串联命中率:    2/3
平均工具调用数:      1.10
token 消耗:          输入 80469 / 输出 11456（共 56 条）
路由正确但后端未跑通: 8 条 → 非路由问题
  剔除后端失败后:      工具路由准确率 18/29

结论：34 passed / 22 failed
train 29/48 · held-out 5/8
```

---

## ⚠️ 数字可信度：这 22 条失败里，只有一小部分是"模型不会用工具"

按失败原因分类后，结论完全不同：

| 类别 | 条数 | 是什么问题 | 是否反映模型能力 |
| --- | --- | --- | --- |
| **A. 后端未跑通** | 8 | `search_policy` 返回 `FAILED / NO_KNOWLEDGE_BASE`：开发库里**一条制度数据都没有**（`document_chunks` 0 行、`knowledge_bases` 0 行）。**已排除 Milvus 因素**——补起 Milvus 后仍失败，才定位到是无数据 | ❌ 数据/环境问题 |
| **B. 黄金集期望不合理** | 约 7 | 话术没给出 `case_id`，却期望直接调 `get_case_summary` / `get_approval_status` / 写工具。模型只能先问"您指的是哪个案件？"——**模型是对的** | ❌ 评测集问题 |
| **C. 真实语义混淆** | 2 | `policy-02`/`policy-h2`："找政策原文"被判给 `search_policy` 而非 `get_policy_source` | ✅ **真实问题** |
| **D. multi 串联偏差** | 1 | `multi-03` 期望 `[get_case_summary, search_policy]`，实际 `[search_policy, search_cases]` | ✅ 真实问题 |

**结论：真正指向"模型/工具描述有问题"的只有 C、D 两类（3 条）。**
A 和 B 占 15 条，是环境与评测集缺陷。**在这 15 条修好之前，48%~62% 这个数字不能用来判断模型能力，更不能据此改工具描述**——否则会针对不存在的问题做优化。

### 修复优先级

1. **A（数据问题）**：在「知识库管理」里为当前单位启用并导入一个制度库，让 `document_chunks`
   有数据。这是**前置的数据准备，不是代码缺陷**。

   > 顺带验证：这 8 条的失败响应本身就是 T3 的产物——`error_code`、`user_message`、
   > `fix`、`retryable=false` 全部到位，中文可读。说明"可自纠错的错误"确实生效了。
   > 另：`validated_params` 里回显了 `detail: "concise"`，证明 T4 的摘要分档也已落地。
2. **B（评测集问题）**：二选一 ——
   - 在话术里补上可解析的案件号（如"EMP-1024 那件事现在到哪一步了"）；或
   - 把这类条目的 `expect` 从 `tool` 改成 `clarify`，并写清 `must_ask`。
   **推荐前者**：真实用户常常真的只说"那件事"，这类条目应该保留，但要在工具描述里说明"没有案件号时先补问"。
3. **C（真问题）**：`get_policy_source` 的描述要强化"要**原文**时用我，只想**找相关条文**时用 `search_policy`"。这类边界正是工具描述该写清的"何时不用"。

---

## 本轮对评测器做的修复（否则数字会更失真）

Windows 侧从未运行过评测器，首次运行暴露 4 个缺陷，已全部修掉：

| # | 缺陷 | 后果 | 修法 |
| --- | --- | --- | --- |
| 1 | DCR 端点硬编码 `{as_url}/register`，实际是 `/oauth/register` | 必然 404，看起来像服务端故障 | 改为从 RFC 8414 元数据发现端点 |
| 2 | MCP 挂载点 `/mcp` 会 307 到 `/mcp/`，重定向是空 body | `JSONDecodeError`，极难定位 | 构造时补尾斜杠；空响应改为带状态码/location 的明确报错 |
| 3 | `max_tokens=200`，而 GLM 带思考模式会先消耗预算 | `content` 为空 → 被记成"模型不会" | 默认 1200（可用 `EVAL_LLM_MAX_TOKENS` 覆盖）；空 content 显式标注原因 |
| 4 | **判分器把"后端调用失败"算进路由准确率** | 模型选对工具却被记失败，严重低估能力 | 保留既有判定（有测试守护），但**单独标记 `routed_ok_but_live_failed` 并在汇总里单列** |

另外 `must_ask` 改为支持 `A|B|C` 同义候选：模型问"哪个案件"而期望写"哪件事"时，不再假阴性。

> 第 4 项说明：既有测试 `test_failed_live_call_fails_the_row` 明确守护"调用失败即判负"，
> 这是有意为之的设计，**不单方面推翻**；改为在 reason 与汇总里把两类失败分开呈现。

## 判分规则（防口径漂移）

- `expect=tool`：模型选对工具即过；**live 调用失败仍判负**（既有约定，有测试守护），
  但 reason 会标明"路由正确；真实调用未成功 —— 后端问题，非路由错误"，并在汇总里单列计数。
  live 未验证（工具需要真实案件 id）**不**判负。
- `expect=clarify`：`must_ask` 逐项命中即过；每项可写 `A|B|C` 同义候选，避免"问对了但字面没对上"。
- `expect=refuse`：调用工具即不过；"拒绝但编了数据"也算不过。
- `expect=multi`：期望工具集合必须**全部**被选中，部分串联不算命中。
- 写工具不真调（评测是客户端，不该制造审批记录）；读工具真调一次验证契约。

## 复跑命令

```bash
# 1) 依赖（不需要 build）
docker compose up -d postgres redis minio etcd milvus

# 2) 服务（或用 docker compose，需能访问 Docker Hub）
set -a && . ./.env && set +a
uvicorn app.main:app       --host 127.0.0.1 --port 8001 &
uvicorn app.oauth.main:app --host 127.0.0.1 --port 8002 &

# 3) 评测
set -a && . ./.env && set +a
python scripts/eval_tool_routing.py --as-url http://localhost:8002 --mcp-url http://localhost:8001/mcp
```

> 注意：URL 用 `localhost`（不是 `127.0.0.1`），否则会被 307 重定向；挂载点尾斜杠已由脚本自动补齐。

## held-out 的用途

train 的 48 条可以用来调工具描述；held-out 的 8 条**不参与**任何描述调优，
只在每次描述版本（`DESCRIPTIONS_VERSION`）变更后复测 —— 它们才是泛化数字。

**下次复测的进入条件**：先修掉 A（起 Milvus）与 B（黄金集话术补案件号），
否则数字仍会被环境问题与评测集缺陷稀释，无法反映真实的描述改动效果。
