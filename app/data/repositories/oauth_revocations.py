"""``oauth_revoked_tokens`` 的**读取**侧 —— 授权服务器与资源服务器共用的唯一一处。

为什么值得单独抽出来
--------------------
撤销是这套令牌体系里唯一"服务端主动作废已签发凭据"的机制。写入侧只有 AS
（``app/oauth/tokens.py`` 的 ``_record_revocation``），读取侧却有两处：

- AS 自己的内省端点（回答"这把令牌还有效吗"）；
- RS 的 ``/mcp`` 校验（每个请求都要回答同一个问题）。

两处若各写一遍条件，任何一处漏掉 ``family`` 粒度，撤销就会**静默失效一半**：用户按下
"撤销授权"之后 refresh token 立刻不可用（那条路径查的是 ``oauth_tokens.revoked_at``），
而已经签发的 access token 却继续有效到自然过期。这个失败模式没有观测手段 ——
调用方看到的一切都正常。所以判据只有一份，写在这里。

这张表没有 RLS（理由见 ``app/data/models/oauth.py``），因此调用它不需要租户上下文：
校验发生的时刻正是还不知道租户是谁的时刻。
"""

from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models.oauth import (
    REVOCATION_KIND_FAMILY,
    REVOCATION_KIND_JTI,
    OAuthRevokedToken,
)


async def is_revoked(session: AsyncSession, *, jti: str, family_id: str) -> bool:
    """这把 access token（或它所属的那条授权会话）是否已被撤销。

    两个粒度任一命中即算撤销：``jti``（单把令牌）与 ``family``（整条 refresh 轮换链）。
    查 ``family`` 是必要的，而不是保险 —— 撤销请求通常只带当前这一把 access token 或
    那把 refresh token，而用户的意图显然是整次授权。
    """
    conditions = []
    if jti:
        conditions.append(and_(OAuthRevokedToken.kind == REVOCATION_KIND_JTI, OAuthRevokedToken.value == jti))
    if family_id:
        conditions.append(and_(OAuthRevokedToken.kind == REVOCATION_KIND_FAMILY, OAuthRevokedToken.value == family_id))
    if not conditions:
        # 两个标识都为空：没有任何可比的取值，不能因此判定"已撤销"。
        return False
    result = await session.execute(select(OAuthRevokedToken.value).where(or_(*conditions)))
    return result.first() is not None
