"""端到端核验：`search_policy` 是否把**检索降级**透出到调用方。

为什么需要它
------------
`Retriever._hybrid` 允许一条腿坏掉后继续作答（dense / sparse 是独立两条腿，
这个设计本身是对的），但降级会改变**排序依据**：只剩 sparse 时排序退化成纯关键词
匹配，字面命中高频词的文档会压过语义上真正相关的那一份。

2026-09-16 事故：dense 腿因 embedding 401 消失，`search_policy("公司的年假是怎么规定的？")`
的 top-1 从《职工带薪年休假条例》变成一份酒店工程部技能考核表，而响应照回
`outcome: FOUND` + 一段自信摘要。修复后降级必须出现在响应契约里。

本脚本从**真实 MCP 出口**核验这件事（不是直接调函数 —— 信封构造时按白名单挑字段
就会把标记丢掉，那种丢失在工具层测试里看不出来）。

怎么制造"故障态"
----------------
把 `.env` 的 `EMBEDDING_BASE_URL` 临时指向一个不可达端点后重启 app，例如：

    EMBEDDING_BASE_URL=http://127.0.0.1:9/v1

⚠️ 注意 `/api/ready` **不能**用来确认这个故障：它的 `embedding` 是**配置存在性**检查
（`app/access/routes/health.py`），不是可达性探测 —— URL 配了但连不通时它照报 `ok`。

预期：健康态下响应里**不出现** `retrieval_degraded`；故障态下出现 `["dense"]`、
`retrieval_note`，且 `summary` 以 `【注意】` 开头。

用法
----
    .venv/bin/python scripts/verify_retrieval_degradation.py
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

TENANT = "eval-runner"
DSN = "postgresql://hrbp:hrbp_password@localhost:5433/hrbp_workbench"
AS_URL = "http://localhost:8002"
MCP_URL = "http://localhost:8001/mcp"
SCOPE = "hrb:profile:read hrb:policy:read hrb:case:read hrb:approval:read hrb:case:propose"

EMAIL = f"vd-{secrets.token_hex(4)}@hrbpilot.test"
PASSWORD = secrets.token_urlsafe(16)
USER_ID = str(uuid.uuid4())
os.environ["EVAL_TENANT_ID"] = TENANT

import asyncpg  # noqa: E402
import bcrypt  # noqa: E402
import httpx  # noqa: E402

import scripts.eval_tool_routing as etr  # noqa: E402

etr.EVAL_EMAIL = EMAIL
etr.EVAL_PASSWORD = PASSWORD
etr.LOGIN_TENANT = TENANT

QUERIES = ["公司的年假是怎么规定的？", "离职时未休年假怎么折算？"]


async def seed() -> None:
    connection = await asyncpg.connect(DSN)
    try:
        await connection.execute(f"SET app.tenant_id='{TENANT}'")
        hashed = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode()
        await connection.execute(
            "INSERT INTO users (id, tenant_id, name, email, hashed_password, role)"
            " VALUES ($1,$2,$3,$4,$5,$6)",
            USER_ID, TENANT, "降级核验用户", EMAIL, hashed, "hrbp",
        )
    finally:
        await connection.close()


async def cleanup() -> None:
    connection = await asyncpg.connect(DSN)
    try:
        await connection.execute(f"SET app.tenant_id='{TENANT}'")
        await connection.execute("DELETE FROM oauth_tokens WHERE user_id=$1", USER_ID)
        await connection.execute("DELETE FROM users WHERE id=$1", USER_ID)
    finally:
        await connection.close()


async def main() -> None:
    await seed()
    try:
        async with httpx.AsyncClient(timeout=90, follow_redirects=False) as http:
            token, _ = await etr.obtain_access_token(http, as_url=AS_URL, scope=SCOPE)
            mcp = etr.McpClient(MCP_URL, token)
            await mcp.initialize(http)
            for query in QUERIES:
                call = await mcp.call_tool(
                    http, "search_policy", {"query": query, "top_k": 3, "detail": "concise"}
                )
                body = call["result"]
                payload = body.get("structuredContent") or json.loads(body["content"][0]["text"])
                print(f"\n=== {query} ===")
                print("  outcome            :", payload.get("outcome"))
                print("  retrieval_degraded :", payload.get("retrieval_degraded", "<不出现=无降级>"))
                print("  retrieval_note     :", (payload.get("retrieval_note") or "<不出现>")[:70])
                print("  summary 前缀        :", payload["summary"][:34])
                head = payload["chunks"][0]
                print("  top1               :", head["source"], "/", head["section"])
    finally:
        await cleanup()


if __name__ == "__main__":
    asyncio.run(main())
