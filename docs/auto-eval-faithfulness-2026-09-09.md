# Faithfulness 指标接入生产评测

日期：2026-09-09

## 根因
`app/scenarios/policy_qa/config.yaml` 的 `eval_metrics` 仅含 `citation_accuracy`、`answer_relevance`，
缺少 `faithfulness`。`orchestrator.py` 调用 `evaluate(..., metrics=self.config.eval_metrics)`（第 153/336 行），
因此 faithfulness 从未被触发（0 条记录）。

## 改动（最小化，未重构）
- `app/scenarios/policy_qa/config.yaml:15-18`：在 `eval_metrics` 列表新增 `- faithfulness`，
  保留原有 `citation_accuracy`、`answer_relevance` 不变。

触发路径：policy_qa 答案生成完成后，`AutoEvaluator.evaluate()` 经 `_schedule_background_task`（fire-and-forget）
异步执行，metrics 现含三项；异常仅在 eval 内部被捕获并打日志，不阻塞主响应。无需新增调用点。

## 实测
后端重启后，登录 `e2e-hrbp-verify01@hrbpilot.test` 并调用 `POST /api/policy-qa/ask` 触发评测。
`eval_results` 现状（is_stub 全为 false）：

| metric | is_stub | count | avg |
|---|---|---|---|
| answer_relevance | f | 34 | 0.850 |
| citation_accuracy | f | 34 | 0.507 |
| faithfulness | f | 3 | 0.167 |

其中一次含真实检索来源的请求，LLM-as-judge 给出 faithfulness=0.5（日志 `evaluation_complete`
scores 含 `faithfulness: 0.5`），为真实 LLM 打分；无来源请求按代码语义返回真实 0.0（非伪造）。
全表 `is_stub=true` 计数为 0，无任何假数据。LLM 未触发 429。

## 测试结果
`.venv/Scripts/python.exe -m pytest tests/rag/test_token_budget.py tests/test_smoke.py -q`
→ 3 passed，无回归。
