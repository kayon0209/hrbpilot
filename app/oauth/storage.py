"""AS 的存储底座：数据库会话 + 不透明凭据的生成与摘要。

只存哈希
--------
授权码与 refresh token 都是 256 位随机值，落库时只留 SHA-256 —— 与仓库既有的
``oauth_nonces.nonce_sha256`` 是同一个做法。这些值会在浏览器地址栏、``Referer``、
客户端日志与代理访问日志里出现，把明文再集中存一份，等于给一次数据库泄漏附赠一批
**可直接使用**的凭据。

摘要刻意**不加盐**：这里的输入空间是 2^256，彩虹表与暴力枚举都不成立；而加盐会让
"按摘要精确查找"无法进行，那恰恰是这些记录唯一的访问路径。

为什么自建会话而不是复用 ``get_db``
-----------------------------------
``app.data.database.get_db`` 要求 ``request.state.tenant_id`` 已就绪。而 AS 查找
客户端与授权码的时刻**正是还不知道租户是谁**的时刻 —— 租户要从查出来的那条记录里读。
AS 自己那四张表也没有 RLS（见 ``app/data/models/oauth.py`` 的说明），不需要租户上下文。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.data.database import get_session_factory

#: 不透明凭据的熵。256 位：即使每秒签发十亿个，穷举命中其中一个也要比宇宙年龄更久 ——
#: 这正是这些值可以只靠"不可猜"来保护（而不依赖 RLS）的前提。
_ENTROPY_BYTES = 32


def new_opaque_value() -> str:
    """生成一个新的不透明凭据（URL 安全、无填充）。"""
    return secrets.token_urlsafe(_ENTROPY_BYTES)


def hash_opaque_value(value: str) -> str:
    """凭据 → SHA-256 十六进制摘要。写入与查询都走它。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    """定长比较。用于 PKCE 校验 —— 那里比较的至少一侧是攻击者可控的字符串。"""
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


@asynccontextmanager
async def oauth_session() -> AsyncIterator[AsyncSession]:
    """AS 的数据库会话：提交或回滚，然后必定关闭。"""
    session = get_session_factory()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
