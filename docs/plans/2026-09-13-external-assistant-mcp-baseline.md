# 外部 Agent MCP 接入 —— WP0 基线记录

- 记录日期：2026-09-13
- 分支：`codex/external-assistant-mcp`
- base SHA：`868c6aecb80d436f5faeb2858d46a0cda018329c`（= 当时的 `origin/main`）
- 上游方案：`docs/plans/2026-09-13-external-assistant-mcp-execution-plan.md`

## 1. 环境指纹

| 项 | 值 |
| --- | --- |
| Python（虚拟环境 `.venv`） | 3.13.15 |
| Python（项目要求 / CI） | `requires-python >=3.12` / CI 用 3.12 |
| Node / pnpm（web 侧，本次未改动） | v22.22.2 / 11.22.0 |
| git | 2.54.0 (Apple Git-157) |
| fastapi | 0.141.1 |
| mcp | 2.2.0 |
| pydantic | 2.13.5 |
| sqlalchemy | 2.0.52 |
| alembic | 1.19.2 |
| python-jose | 3.5.0 |
| pytest | 9.1.1 |
| ruff | 0.16.6 |
| mypy | 2.3.1 |
| aiosqlite | 0.22.1 |
| asyncpg | 0.31.0 |
| redis | 5.3.1 |

无 PostgreSQL / Redis / Milvus / MinIO 运行实例。全套测试在无这些依赖的情况下可跑完
（相关用例被 `skip`），因此本记录**不覆盖** `integration` 标记的用例。

## 2. 基线（WP1 改动之前，`868c6ae` 工作区）

```bash
.venv/bin/python -m pytest -q                       # 553 passed, 50 skipped, 1 warning in 22.96s
.venv/bin/ruff check .                              # 退出码 1（4 errors）
.venv/bin/ruff format --check .                     # 退出码 1（9 files would be reformatted）
.venv/bin/ruff check app tests evaluation           # 退出码 0（All checks passed）
.venv/bin/ruff format --check app tests             # 退出码 0（335 files already formatted）
.venv/bin/mypy app                                  # 退出码 0（Success: no issues found in 229 source files）
```

**基线是"部分红"的** —— 这与上游方案 §6.1 的假设不同。差异来源与处置见
`docs/upgrade/ADR-0002-mcp-resource-server-and-authorization-server.md` 决策七：

- `ruff check .` 的 4 个错误全部在 `scripts/fault_injection_matrix.py`（未使用变量、
  导入顺序、无用的 `noqa`、未使用的 `datetime` 导入）。
- `ruff format --check .` 待格式化的 9 个文件落在 `docs/cost-governance-and-eval.md` 与
  `scripts/`、`evaluation/`；CI **从不**检查这些路径，pre-commit 的 ruff 钩子按默认
  `types_or` 也不含 markdown。
- 因此门禁采用 CI 的真实命令，而不是 `ruff check .` / `ruff format --check .`。

## 3. WP1 之后（本次改动的工作区）

```bash
.venv/bin/python -m pytest -q                       # 583 passed, 50 skipped, 1 warning in 22.38s
.venv/bin/ruff check app tests evaluation           # 退出码 0
.venv/bin/ruff format --check app tests             # 退出码 0（343 files already formatted）
.venv/bin/mypy app                                  # 退出码 0（Success: no issues found in 235 source files）
```

**零回归**：`553 → 583` 的全部增量是本次新增的用例（30 条），失败数在两次运行中均为 0，
`skipped` 恒为 50（环境性跳过，未变）。

| 口径 | 改动前 | 改动后 | 判读 |
| --- | --- | --- | --- |
| passed | 553 | 583 | +30，全部为本次新增 |
| failed | 0 | 0 | 无回归 |
| skipped | 50 | 50 | 环境性跳过，未增加 |
| mypy 源文件数 | 229 | 235 | +6（4 个新模块 + 2 个 `__init__`/1 个新模块计入） |
| ruff format 已格式化文件数 | 335 | 343 | +8 |

## 4. 数据库迁移拓扑

```bash
.venv/bin/alembic heads     # 037_chunk_page_number (head)   —— 单 head
```

- 当前唯一 head 是 `037_chunk_page_number`，下一个可用编号是 **038**。
- 历史上存在 4 个 merge 迁移（`031_merge_runtime_wecom_heads`、
  `033_merge_runtime_wecom_heads`、`034_merge_governed_runtime_heads`）与 3 组**重号**
  （`029` ×2、`030` ×2、`031` ×2）。
- 含义：`alembic heads` 现在是单 head，可以安全直线追加；但**编号不能靠猜**，
  新增迁移必须以 `037_chunk_page_number` 为 `down_revision`，并自行确认编号未被占用。

## 5. 本次未验证的事项（明确列出，不用"应当没问题"代替）

1. **无 PostgreSQL 的运行环境**：迁移的 upgrade / downgrade、RLS 行为、以及
   `integration` 标记的用例全部未在本机执行。上游方案 §13 要求的"空库升级、现有库升级、
   回滚演练"因此**尚无证据**。
2. **无 Redis**：限流链路未实测。
3. **无 Milvus / MinIO**：检索链路的真实依赖未接入（现有测试用 fake retriever）。
4. **web 侧未改动也未运行**：`pnpm lint` / `pnpm test:run` / `pnpm build` 未执行，
   因为本次没有任何前端文件变更。`web/dist` 中的构建产物仍是旧文案。
5. **未做真实客户端联调**：WorkBuddy / Codex / Claude Code 三者的 OAuth 流程属于
   WP2—WP6，本次没有可联调的认证端点。
6. **无并发压测**：`Locust` 用例未运行。

## 6. 复现命令

```bash
cd <repo>
.venv/bin/ruff check app tests evaluation
.venv/bin/ruff format --check app tests
.venv/bin/mypy app
.venv/bin/python -m pytest -q
.venv/bin/alembic heads
```

沙箱或只读 `TMPDIR` 环境下跑 pytest 需显式指定可写的临时目录，否则会在收集阶段
报大量 `error`（与代码无关）：

```bash
.venv/bin/python -m pytest -q --basetemp=/tmp/<可写路径> -p no:cacheprovider
```
