"""应急处置：撤销**所有**外部 Agent 的授权（跨租户）。

什么时候用它
------------
管理端 API（``POST /api/admin/mcp/revoke-all``）是租户级的，而且要先能登录进去。
下面两种情况它覆盖不了：

1. **怀疑凭据大面积泄漏**，需要立刻把所有人的外部 Agent 全部断开；
2. **管理端本身不可用**（认证故障、管理页面挂了），而门必须关上。

这个脚本直接连库操作，不经过任何 HTTP 层 —— 因此它在"应用已经不正常"时仍然可用。

顺序很重要：先关开关，再撤销
----------------------------
脚本第一步打印出应该设置的开关，**但这不会自动改环境变量**（那需要重启进程，
脚本改不动）。真正的顺序建议是：

1. 把 ``MCP_EXTERNAL_ENABLED=false`` 并重启 RS —— 门先关上；
2. 再跑本脚本撤销已签发的凭据。

反过来的话，在新旧进程交替的那段时间里，新连接仍会被接受。

为什么默认是 dry-run
--------------------
这是不可逆的破坏性操作（所有外部 Agent 立刻失效，用户需要重新授权）。误敲一条命令
就让全公司 Agent 掉线，代价远大于多打一个 ``--apply``。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from sqlalchemy import distinct, select

from app.config.settings import settings
from app.data.database import get_session_factory
from app.data.models.oauth import OAuthToken
from app.oauth.tokens import REVOKED_REASON_USER_REVOKED, revoke_family_in_own_session


async def _active_families(tenant_id: str | None) -> list[tuple[str, str, str]]:
    """返回 ``(tenant_id, client_id, family_id)``，只含未撤销的轮换链。"""
    factory = get_session_factory()
    async with factory() as session:
        statement = select(
            distinct(OAuthToken.family_id),
            OAuthToken.tenant_id,
            OAuthToken.client_id,
        ).where(OAuthToken.revoked_at.is_(None))
        if tenant_id:
            statement = statement.where(OAuthToken.tenant_id == tenant_id)
        rows = (await session.execute(statement)).all()
    return [(str(row[1]), str(row[2]), str(row[0])) for row in rows]


async def main() -> int:
    parser = argparse.ArgumentParser(description="撤销全部外部 Agent 授权（默认只预览，加 --apply 才真的执行）")
    parser.add_argument("--apply", action="store_true", help="真的执行撤销；不加则只列出将要撤销的内容")
    parser.add_argument("--tenant", default=None, help="限定单个租户；默认所有租户")
    parser.add_argument(
        "--reason",
        default=REVOKED_REASON_USER_REVOKED,
        help="写入撤销记录的原因，会出现在审计与内省结果里",
    )
    args = parser.parse_args()

    families = await _active_families(args.tenant)
    if not families:
        print("没有需要撤销的授权。")
        return 0

    by_tenant: dict[str, list[tuple[str, str]]] = {}
    for tenant_id, client_id, family_id in families:
        by_tenant.setdefault(tenant_id, []).append((client_id, family_id))

    print(f"将要撤销 {len(families)} 个安装实例，涉及 {len(by_tenant)} 个租户：")
    for tenant_id, items in sorted(by_tenant.items()):
        clients = sorted({client for client, _ in items})
        print(f"  {tenant_id}: {len(items)} 个实例，客户端 {', '.join(clients)}")

    if not args.apply:
        print()
        print("这是预览。确认无误后加 --apply 执行。")
        print()
        print("执行前请先确认回滚开关已打开（需要重启进程，本脚本改不了）：")
        print(f"    MCP_EXTERNAL_ENABLED=false   # 当前值：{settings.mcp_external_enabled}")
        print("顺序是「先关开关、再撤销」——反过来的话，新旧进程交替期间新连接仍会被接受。")
        return 0

    for tenant_id, client_id, family_id in families:
        await revoke_family_in_own_session(family_id, reason=args.reason, tenant_id=tenant_id)
        print(f"  已撤销 {tenant_id} / {client_id} / {family_id}")

    print()
    print(f"完成：{len(families)} 个安装实例已撤销（{datetime.now(UTC).isoformat()}）")
    print("确认 MCP_EXTERNAL_ENABLED=false 已生效；否则新授权仍会被接受。")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
