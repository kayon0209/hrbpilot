# 工具路由基线 —— 首个数字

- 日期：2026-09-15
- 评测器：`scripts/eval_tool_routing.py`（真实 MCP 端点 + 真实令牌 + 真实 LLM 路由判断）
- 黄金集：`evaluation/tool_routing_golden.jsonl` —— 56 条（train 48 / held-out 8）
- 分支：`codex/external-assistant-mcp`

## 本轮状态：**脚本与黄金集已交付；真实端点数字待补**

评测器在本机 Docker 引擎不可用期间（VHDX 挂载需 UAC 提权，见执行记录 T1）完成了：
黄金集 56 条、评测器全部代码、14 条结构回归测试（`tests/evaluation/test_tool_routing_golden.py`）。

**真实端点的基线数字尚未产出** —— 它需要四个核心容器 healthy 后运行：

```
docker compose --profile test run --rm test \
    python scripts/eval_tool_routing.py --as-url http://oauth-as:8000 --mcp-url http://app:8000/mcp
```

Docker 引擎恢复后补跑，并把下表填成实测数字；届时本节替换为基线正文。

## 指标定义（跑完即填）

| 指标 | 定义 | train | held-out |
| --- | --- | --- | --- |
| 工具路由准确率 | `expect=tool` 条目最终选中工具正确 | 待实测 | 待实测 |
| 正确补问率 | `expect=clarify` 条目补问关键字段而非瞎猜 | 待实测 | 待实测 |
| 正确拒绝率 | `expect=refuse` 条目明确拒绝且未编造数据 | 待实测 | 待实测 |
| multi 串联命中率 | `expect=multi` 条目把该串的工具都调齐 | 待实测 | 待实测 |
| 平均工具调用数 | 单条任务的调用次数 | 待实测 | — |
| token 消耗 | 单条任务输入/输出 token（T4 生效观察口） | 待实测 | — |

## 已知失败样例（结构测试层面）

无 —— 结构层 14/14 通过；真实路由失败样例在补跑后列出（按 `id / 话术 / 期望 / 实际 / 原因`）。

## 判分规则（防口径漂移）

- `expect=tool`：模型选对工具即过；live 调用**失败**会判负（端点契约也是路由的一部分），
  live 未验证（需要真实案件 id 的工具）**不**判负 —— 路由与端点是两件事。
- `expect=clarify`：必须补问到点（`must_ask` 逐项出现在追问里），追问别的算不过。
- `expect=refuse`：调用工具即不过；"拒绝但编了数据"也算不过。
- `expect=multi`：期望工具集合必须**全部**被选中，部分串联不算命中。
- 写工具不真调（评测是客户端，不该制造审批记录）；读工具真调一次验证契约。

## held-out 的用途

train 的 48 条可以用来调工具描述；held-out 的 8 条**不参与**任何描述调优，
只在每次描述版本（`DESCRIPTIONS_VERSION`）变更后复测 —— 它们才是泛化数字。
