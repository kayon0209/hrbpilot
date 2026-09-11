# HRBPilot 依赖降级矩阵（故障注入实测）

> 由 `scripts/fault_injection_matrix.py` 实际停/起容器后探测得出，非推断。
> 探测路径：liveness `/api/health`、readiness `/api/ready`、业务读 `/api/notifications`。

## 1. 依赖不可用时的系统行为

| 故障场景 | liveness | readiness 总体 | 未鉴权读 | 已鉴权读 | 判定 |
| --- | --- | --- | --- | --- | --- |
| 基线（依赖全可用） | 200 | `degraded`（HTTP 200） | 401 | 200 | 正常 |
| Redis 不可用 | 200 | `not_ready`（HTTP 503）<br>关键：redis | 401 | 429 | 鉴权仍生效(401)，已登录请求 429 → 限流 fail-closed |
| PostgreSQL 不可用 | 200 | `not_ready`（HTTP 503）<br>关键：database | 401 | **异常抛出**（部署态由 error handler 转 5XX） | 鉴权仍生效(401)，已登录请求抛异常 → 故障信号未被伪装 |
| Redis + PostgreSQL 同时不可用 | 200 | `not_ready`（HTTP 503）<br>关键：database, redis | 401 | 429 | 鉴权仍生效(401)，已登录请求 429 → 限流 fail-closed |

## 2. readiness 逐依赖状态

依赖按**影响半径**分两级（2026-09-11 起），状态用词也随级别不同：

| 级别 | 依赖 | 不可用时状态 | 对整体的影响 |
| --- | --- | --- | --- |
| critical | database, redis | `error` | `not_ready` + HTTP 503（应从流量摘除） |
| optional | milvus, minio, embedding | `unavailable` | `degraded` + HTTP 200（仍可服务） |

| 场景 | database | embedding | milvus | minio | redis |
| --- | --- | --- | --- | --- | --- |
| 基线（依赖全可用） | ok | ok | unavailable | unavailable | ok |
| Redis 不可用 | ok | ok | unavailable | unavailable | error |
| PostgreSQL 不可用 | error | ok | unavailable | unavailable | ok |
| Redis + PostgreSQL 同时不可用 | error | ok | unavailable | unavailable | error |

## 3. 模型服务不可达

注入方式：`LLM_BASE_URL=http://127.0.0.1:9/v1`（必然 refused，且绕过本机 HTTP 代理）

```
RAISED APIConnectionError Connection error.
ELAPSED 8.6 s
```

## 4. 读数注意事项

1. **未鉴权请求在任何依赖故障下仍是 401**：中间件顺序是 `Auth → RBAC → RateLimit → Tenant`，鉴权先于限流。所以限流 fail-closed 的代价是**可用性**，不是安全性 —— 它不会让任何人绕过鉴权。
2. **readiness 是分级的，别只看「degraded 就是坏了」**：关键依赖故障 ⇒ `not_ready` + HTTP 503；仅可选依赖不可用 ⇒ `degraded` + HTTP 200，服务照常工作。修复前（2026-09-11 前）两种情况的权重相同，导致 milvus/minio 一旦没装，`/api/ready` 就恒为 `degraded` —— 拿去做 K8s readinessProbe 会让服务永远不被判定为就绪。**分级后这个坑已消除。**
3. **区分「可选依赖没装」与「关键依赖挂了」靠 `critical_failed` 字段**，不要靠总体状态猜：`critical_failed` 为空 = 服务仍可用。
4. **表格里的「异常抛出」是好事**：它代表故障信号没有被伪装成别的错误。2026-09-10 修复前，PostgreSQL 宕机被测成 **429（限流）** —— 因为 Redis 客户端一旦失败就在同一事件循环内永久短路，限流器持续 fail-closed。排查方向会被整体带偏。修复后这里变成异常抛出，信号才对得上故障。

---

*由脚本自动生成，请勿手改；改行为请改代码后重跑。*