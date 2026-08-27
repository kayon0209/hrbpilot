# HR Case Agent Runbook

> 适用版本：Phase 7 完成后的 main。所有命令在仓库根目录、Python 3.12 下执行。

## 1. 环境准备

```bash
pip install -e ".[dev]"
# PostgreSQL（容器发布在宿主 5433，避开本机 PG 服务占用的 5432）
docker compose up -d postgres redis
# 迁移（007 hr_case 表 + 008 token_ledger）
DATABASE_URL="postgresql+asyncpg://hrbp:hrbp_password@localhost:5433/hrbp_workbench" python -m alembic upgrade head
```

注意：`.env` 里的 `DATABASE_URL` 若指向 5432 会撞上宿主机本地 PostgreSQL 服务（错误表现为 `InvalidPasswordError for user "hrbp"`）。本地运行时用环境变量覆盖为 5433。

## 2. 核心状态机

```
NEW → TRIAGED → EVIDENCE_READY → PLAN_READY → AWAITING_APPROVAL
    → EXECUTING → RESOLVED | FAILED
FAILED → EXECUTING（同 request_id 不可能：需新审批）| AWAITING_APPROVAL（重新审批后重试）
任意状态 → HANDED_OFF（转人工，终态）
```

状态只能经 `HRCaseService.transition_case` 修改（内部走 `state.transition`），API 层直接改 `status` 会被状态机拒绝（422 INVALID_CASE_TRANSITION）。

## 3. 典型操作

### 3.1 建单 → 计划 → 运行 → 审批 → 执行

```bash
BASE=http://localhost:8001/api/v1/hr-cases
TOKEN=<JWT>   # role 需为 hr_manager / admin 才能审批与执行

# 1) 建单
curl -X POST $BASE -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"subject_ref":"EMP-SYN-101","category":"overtime","title":"加班费支付争议","risk_level":"MEDIUM"}'

# 2) 生成计划（LLM 提案可空，走确定性 triage 计划）
curl -X POST $BASE/<case_id>/plan -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"goal":"处理加班费投诉"}'

# 3) 运行计划：读工具立即执行，写工具停在审批门（返回 approval_id）
curl -X POST $BASE/<case_id>/run -H "Authorization: Bearer $TOKEN"

# 4) 人工审批（与执行严格分成两个请求）
curl -X POST $BASE/<case_id>/approve -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"approval_id":"<approval_id>","decision":"approve","reason":"情况属实"}'

# 5) 执行已批准的写工具（幂等：同 request_id 不产生第二次副作用）
curl -X POST $BASE/<case_id>/execute -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"approval_id":"<approval_id>","request_id":"<uuid>" }'

# 6) 审计轨迹与运行回放
curl $BASE/<case_id>/events -H "Authorization: Bearer $TOKEN"
curl $BASE/<case_id>/runs/<run_id> -H "Authorization: Bearer $TOKEN"
```

### 3.2 故障排查

| 现象 | 根因 | 处理 |
| --- | --- | --- |
| `422 INVALID_CASE_TRANSITION` | 跳状态或终态后再变更 | 按状态机走合法路径 |
| `409 APPROVAL_INVALID: expired` | 审批超时（默认 1h TTL） | 重新 `plan/run` 生成新审批 |
| `409 APPROVAL_INVALID: requires APPROVED` | 未审批或已消费（CONSUMED） | 必须先 `approve`；失败的执行也不能复用旧审批 |
| `501 TOOL_EXECUTOR_MISSING` | 写工具无生产执行器注册 | 检查 `agent_loop.register_tool_executor` 启动注册 |
| `429 RATE_LIMIT_EXCEEDED` | Redis 滑动窗口限流（60/min per tenant、30/min per user） | 排查异常调用方；Redis 不可用时开发模式 fail-open |
| `InvalidPasswordError` 连 PG | 5432 被宿主机本地 PG 占用 | `DATABASE_URL` 指向 5433 |

### 3.3 安全红线（运维必读）

- 审批人 `approver_id` 一律取自 JWT（服务端），请求体声明的身份不采信。
- 写工具的执行与审批是**两个请求**；不存在「批准并执行」的合并端点。
- 审批与执行的参数绑定用规范化后的哈希（`input_hash`）比对，替换参数的执行会 409。
- `case_events` 只追加；应用代码不提供更新/删除路径。
- `subject_ref` 仅使用合成标识（如 `EMP-SYN-101`），禁止写入真实员工档案。

## 4. 评测

```bash
python -m pytest tests/hr_case tests/evaluation -q   # 领域 + 轨迹评测门禁
python scripts/demo_hr_case.py                        # 三旅程演示（无需 LLM/网络）
python evaluation/run_golden_eval.py                  # 离线 golden 评测（注意模式标注）
```

轨迹门禁（Phase 6）：未授权写 = 0、重复副作用 = 0、高风险转人工 ≥ 0.95、误升级 ≤ 0.10、写审批门 = 1.0。任何门禁失败先修复再发布。
