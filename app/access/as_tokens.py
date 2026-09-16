"""Resource Server 侧：AS 签发令牌的校验，以及**凭据来源的分流**。

为什么需要它
------------
``app/access/tokens.py`` 回答的是"这个**平台自签**令牌说明了什么身份"（HS256，
`iss == aud == hrbp-ai-workbench`）。外部 Agent 的令牌完全是另一回事：由授权服务器用
ES256 签名，``aud`` 绑定到本部署的 MCP resource，而且**可以被撤销**。校验它需要
两样 access 层本就具备能力、但此前没有用武之地的东西 —— 一次对 AS JWKS 的 HTTP
取值，和一次对撤销表的数据库查询。

放在 ``access`` 而不是 ``mcp``：HTTP 中间件（``app/access/middleware/auth.py``）必须
在请求进入工具层**之前**完成校验，而本层有一条既有约定 —— 中间件不依赖
``app.mcp.*``（该约定在 ``app/access/tokens.py`` 顶部有说明）。

为什么要有"分流"这一个入口
--------------------------
一个 ``Authorization`` 头的值是哪种令牌，只能从**未验签**的 ``iss`` 判断 —— 这是
JOSE 生态的标准做法（与按 ``kid`` 选公钥同类）。判断本身不构成信任：分流之后，
选中的那条路径会**完整地**重新验证 ``iss``（含签名覆盖的那一份），所以"自称是某个
受信任 AS"的伪造令牌在验签那一步就死了。

把分流放在这里、而不是让中间件与 MCP 出口各自判一次，是因为"同一判定写两遍"正是
WP1 修掉的那类缺陷（两条出口结论相反）。入口只有一个：
``resolve_access_claims``。

三类校验，三个独立的事实
------------------------
1. **签名与 claim**（``_decode_signed_claims``）：``kid`` 选公钥验签；``iss`` 命中信任
   列表；``aud`` 逐字节等于 ``settings.mcp_resource_url``；``typ`` 是 ``at+jwt``
   （RFC 9068 §2.1）；``exp`` 未过。任何一条不过都退化成 ``MALFORMED``。
2. **撤销**：access token 是无状态的，签出去之后 AS 手里没有它的记录 —— 唯一的撤销
   手段就是 RS 在验签后多查一次撤销表（``app/data/models/oauth.py`` 的
   ``oauth_revoked_tokens``），按 ``jti``（单把令牌）与 ``family_id``（整条授权会话）
   两个粒度比对。
3. **实时授权状态**：客户端必须已注册、未禁用且未被租户拉黑；用户必须仍然
   启用，而且当前角色和 ``auth_version`` 必须与令牌一致。这使禁用账号、角色变更
   和客户端撤销对已签发的访问令牌立即生效。
4. **客户端授权上限**：从 ``oauth_clients.scope`` 读该客户端**注册**的范围，与令牌自述
   的 scope 取交集才是有效权限（``McpPrincipal.effective_scopes``）。不认识的客户端或缺失的
   授权上限都按拒绝处理，不再回退到令牌自述范围。

第 3 条是纵深防御，不是唯一防线
-------------------------------
令牌的 scope 在授权阶段已经被 AS 裁剪到客户端注册范围内（参见
``app/oauth/routes/authorize.py`` 的 ``invalid_scope`` 分支），所以正常情况下交集等于
令牌自身。RS 再查一次的意义是：**AS 有 bug 或配置漂移时，RS 不会跟着一起放宽**。

对 RS 不认识的客端不做宽松兼容：即使令牌签名正确，也无法证明该客户端在当前
租户的实时授权上限，因此必须失败关闭。第三方 AS 要接入时，需先通过受控同步
将客户端状态和范围写入本地注册表。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import httpx
from jose import JWTError
from jose import jwt as jose_jwt
from sqlalchemy import select

from app.access.scopes import Scope, parse_scopes
from app.access.tokens import AccessClaims, TokenRejection, read_access_claims
from app.config.settings import settings
from app.data.database import get_session_factory
from app.data.models.oauth import OAuthClient
from app.data.repositories.oauth_revocations import is_revoked
from app.shared.logger import get_logger

logger = get_logger(__name__)

#: 固定的验签算法。与 AS 的签发算法（``app/oauth/keys.py`` 的 ``ALGORITHM``）必须一致。
#: **不接受**令牌头里的 ``alg`` 作为选择依据 —— 那是攻击者可控输入，把它当选择器就是
#: 经典的算法混淆漏洞。这里先校验头里的 ``alg`` 是否等于本常量，再用本常量验签。
ALGORITHM = "ES256"

#: RFC 9068 §2.1 给 JWT 形式的 access token 规定的 ``typ``。校验它才能把"访问令牌"
#: 与"别的什么 JWT"分开 —— 将来若加入 ID Token，两者不会互相冒充。
ACCESS_TOKEN_TYPE = "at+jwt"

#: RFC 8414 §3.1：issuer 无路径分量时 well-known 挂在根上；带路径时插在 host 与 path 之间。
_WELL_KNOWN_METADATA_PATH = "/.well-known/oauth-authorization-server"

#: JWKS 与元数据的缓存时长。与 AS 端 ``Cache-Control`` 同一个量级：轮换后最迟这么久
#: 生效；kid 未命中时会**强制刷新一次**，所以实际感知延迟远小于这个值。
_DOCUMENT_CACHE_SECONDS = 300.0
_HTTP_TIMEOUT_SECONDS = 5.0
_MAX_DOCUMENT_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class AsAccessClaims:
    """已通过全部校验的 AS access token 载荷。

    ``client_ceiling`` **不是** ``None``：上游构造 ``McpPrincipal`` 时该字段必填
    （``app/mcp/auth/principal.py`` 的构造期约束），而"没有上限的外部凭据"正是那条
    约束要禁止的东西。未知客户端时的取值规则见模块头部。
    """

    issuer: str
    client_id: str
    user_id: str
    tenant_id: str
    role: str
    email: str
    scopes: frozenset[Scope]
    client_ceiling: frozenset[Scope]
    #: 一次授权发放的 refresh 轮换链。RS 侧把它当作"安装实例"的等价物 ——
    #: 见 ``app/mcp/auth/adapters.py``。
    family_id: str
    token_id: str


@dataclass(frozen=True, slots=True)
class _CachedDocuments:
    keys: dict[str, dict[str, Any]]
    fetched_at: float


_documents: dict[str, _CachedDocuments] = {}
_locks: dict[str, asyncio.Lock] = {}


def reset_document_cache() -> None:
    """清空 AS 文档缓存。测试与密钥轮换后的强制重取共用。"""
    _documents.clear()


def accepts_issuer(issuer: str) -> bool:
    """这个 issuer 是否在本部署的信任列表里（``MCP_AUTHORIZATION_SERVERS``）。

    比对前把两端的尾斜杠都去掉：配置侧已经在解析时归一化，而令牌里的 ``iss`` 由
    签发方决定，``https://as.example.com`` 与 ``https://as.example.com/`` 在实践中是
    同一个 issuer。这不是"宽松匹配"—— 仍然是完整字符串相等，只是抹掉一个不承载
    语义的字符（RFC 9207 要求的 mix-up 防御针对的是**不同** issuer，不是同一个
    issuer 的两种写法）。
    """
    if not issuer:
        return False
    normalized = issuer.rstrip("/")
    return any(normalized == candidate for candidate in settings.authorization_servers)


def unverified_issuer(token: str) -> str | None:
    """**未验签**地读出 ``iss``，仅用于决定用哪条校验路径。"""
    try:
        claims = jose_jwt.get_unverified_claims(token)
    except JWTError:
        return None
    except (TypeError, ValueError):
        return None
    if not isinstance(claims, dict):
        return None
    issuer = claims.get("iss")
    return issuer if isinstance(issuer, str) else None


def _normalized_issuer_match(declared: str, expected: str) -> bool:
    return declared.rstrip("/") == expected.rstrip("/")


def _metadata_url(issuer: str) -> str:
    """RFC 8414 §3.1 的元数据 URL。"""
    parts = urlsplit(issuer)
    return urlunsplit((parts.scheme, parts.netloc, _WELL_KNOWN_METADATA_PATH + parts.path.rstrip("/"), "", ""))


#: 明文 http 放行的环回主机（本地联调：AS 与 RS 同机时没有证书）。
_LOOPBACK_HTTP_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _configured_internal_host() -> str:
    """``OAUTH_INTERNAL_BASE_URL`` 的主机名（未配置时为空串）。

    它和 issuer 一样是**运维配置**，不是外部文档里的值。容器/服务网格里 AS 走的是
    明文 http（没有证书），所以这条白名单必须容纳它 —— 否则容器部署下 RS 永远取不到
    元数据与 JWKS，**所有**外部令牌都会被判 ``malformed``，症状是"外部 Agent 全部
    401"，而日志里真正的线索是旁边那条 ``as_document_url_rejected``。
    """
    base = settings.oauth_internal_base_url.strip()
    if not base:
        return ""
    return (urlsplit(base).hostname or "").lower()


def _is_fetchable_document_url(url: str) -> bool:
    """文档 URL 的形式检查：绝对 http(s)、无 userinfo、非环回必须 https。

    取地址来自**配置的 issuer** 或**配置的容器内地址**（都是可信输入），因此不需要
    CIMD 那套 SSRF 防护；但"明文 http 只对环回与显式配置的容器内主机放行"这条仍然
    要有：一个任意的明文 ``jwks_uri`` 意味着任何能在链路上改写响应的人都能塞进自己
    的公钥。放行名单只有两个来源，且都是运维写死在配置里的。
    """
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return False
    if parts.username or parts.password:
        return False
    host = (parts.hostname or "").lower()
    if not host:
        return False
    if parts.scheme == "http":
        internal = _configured_internal_host()
        return host in _LOOPBACK_HTTP_HOSTS or (bool(internal) and host == internal)
    return True


async def _fetch_json(url: str) -> object | None:
    """取一份 JSON 文档；任何失败都返回 ``None``（调用方 fail-closed）。

    禁用重定向、限时、限量。取不到不等于"令牌无效"，但对调用方而言结果相同 ——
    验不过的令牌不能放行，所以这里不区分，只把原因写进日志。
    """
    if not _is_fetchable_document_url(url):
        logger.warning("as_document_url_rejected", url=url)
        return None
    try:
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT_SECONDS,
            follow_redirects=False,
            headers={"Accept": "application/json"},
        ) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        logger.warning("as_document_fetch_failed", url=url, error=type(exc).__name__)
        return None
    if response.status_code != 200:
        logger.warning("as_document_http_error", url=url, status=response.status_code)
        return None
    if len(response.content) > _MAX_DOCUMENT_BYTES:
        logger.warning("as_document_too_large", url=url, size=len(response.content))
        return None
    try:
        document: object = response.json()
    except ValueError:
        logger.warning("as_document_not_json", url=url)
        return None
    return document


def _lock_for(issuer: str) -> asyncio.Lock:
    lock = _locks.get(issuer)
    if lock is None:
        lock = asyncio.Lock()
        _locks[issuer] = lock
    return lock


async def _fetch_signing_keys(issuer: str) -> dict[str, dict[str, Any]] | None:
    """按 RFC 8414 元数据找到 ``jwks_uri``，取回 JWKS 并建成 ``kid → JWK`` 映射。"""
    internal_base = settings.oauth_internal_base_url.rstrip("/")
    fetch_base = internal_base if internal_base and _normalized_issuer_match(issuer, settings.oauth_issuer) else issuer
    metadata = await _fetch_json(_metadata_url(fetch_base))
    if not isinstance(metadata, dict):
        return None
    declared_issuer = metadata.get("issuer")
    if not isinstance(declared_issuer, str) or not _normalized_issuer_match(declared_issuer, issuer):
        # RFC 9207 的 mix-up 防御：拿到的文档必须声称自己属于我们请求的那个 issuer。
        # 不查这一条，一次 DNS 劫持或错误的反代配置就能让 RS 用别人的公钥验签。
        logger.warning("as_metadata_issuer_mismatch", expected=issuer, declared=declared_issuer)
        return None
    jwks_uri = metadata.get("jwks_uri")
    if not isinstance(jwks_uri, str) or not jwks_uri:
        logger.warning("as_metadata_without_jwks_uri", issuer=issuer)
        return None
    fetch_jwks_uri = jwks_uri
    if internal_base and _normalized_issuer_match(issuer, settings.oauth_issuer) and jwks_uri.startswith(issuer):
        fetch_jwks_uri = f"{internal_base}{jwks_uri[len(issuer) :]}"
    document = await _fetch_json(fetch_jwks_uri)
    if not isinstance(document, dict):
        return None
    raw_keys = document.get("keys")
    if not isinstance(raw_keys, list):
        return None
    keys: dict[str, dict[str, Any]] = {}
    for entry in raw_keys:
        if not isinstance(entry, dict):
            continue
        kid = entry.get("kid")
        if isinstance(kid, str) and kid:
            keys[kid] = entry
    if not keys:
        logger.warning("as_jwks_empty", issuer=issuer)
        return None
    return keys


async def _signing_keys_for(issuer: str, *, refresh: bool = False) -> dict[str, dict[str, Any]] | None:
    """取该 issuer 的公钥集合（带缓存）。

    ``refresh=True`` 用于"``kid`` 在缓存里找不到"的情形 —— 那通常意味着 AS 刚轮换过
    密钥。此时**最多**强制重取一次；仍然找不到就走拒绝，不进入重试循环。
    并发去重的做法：拿到锁之后再比一次时间戳，若已有协程在等待期间刷新过就直接复用，
    避免一场 N 个请求同时打同一个 AS 的雪崩。
    """
    started_at = time.monotonic()
    cached = _documents.get(issuer)
    if cached is not None and not refresh and started_at - cached.fetched_at < _DOCUMENT_CACHE_SECONDS:
        return cached.keys
    async with _lock_for(issuer):
        cached = _documents.get(issuer)
        if cached is not None:
            if cached.fetched_at >= started_at:
                # 等锁期间已被别的协程刷新。
                return cached.keys
            if not refresh and started_at - cached.fetched_at < _DOCUMENT_CACHE_SECONDS:
                return cached.keys
        fresh = await _fetch_signing_keys(issuer)
        if fresh is None:
            # 刷新失败时**继续用旧公钥**：一次超时不应当让所有在途令牌集体失效
            # （那会是一次由网络抖动触发的全站登出）。旧公钥仍在过期时间内。
            return cached.keys if cached is not None else None
        _documents[issuer] = _CachedDocuments(keys=fresh, fetched_at=time.monotonic())
        return fresh


async def _load_registry_state(
    *,
    jti: str,
    family_id: str,
    client_id: str,
    tenant_id: str,
    user_id: str,
    role: str,
    auth_version: int,
) -> tuple[bool, frozenset[Scope] | None]:
    """Atomically re-check revocation, client ceiling, and current identity.

    返回 ``(revoked, ceiling)``；``ceiling`` 为 ``None`` 表示客户端不在本库（见模块头部）。

    The signed claims have already supplied a verified tenant and user at this
    point, so the session is tenant-bound before querying the RLS user table.
    """
    from app.data.models.oauth import OAuthClientBlock
    from app.data.models.user import User

    session = get_session_factory()()
    session.info["tenant_id"] = tenant_id
    try:
        if await is_revoked(session, jti=jti, family_id=family_id):
            return True, None
        client = await session.get(OAuthClient, client_id)
        if client is None or client.status != "active":
            return True, None
        block = await session.get(OAuthClientBlock, (tenant_id, client_id))
        if block is not None and block.blocked:
            return True, None
        user = await session.scalar(select(User).where(User.id == user_id, User.tenant_id == tenant_id))
        if user is None or not user.is_active or user.role != role or user.auth_version != auth_version:
            return True, None
        scope_value = client.scope
    finally:
        await session.close()
    if scope_value is None:
        return False, None
    return False, parse_scopes(str(scope_value))


async def _decode_signed_claims(token: str, *, issuer: str, kid: str) -> dict[str, Any] | None:
    """用 ``kid`` 对应的公钥验签并校验 ``aud`` / ``exp``。

    ``kid`` 未命中时强制刷新一次公钥再试 —— 这是密钥轮换的生效路径。

    ``iss`` 的比对**不在这里**（python-jose 的 ``issuer=`` 参数只做逐字符相等，与本模块
    对尾斜杠的归一化不一致，两处各有一套判定就会漂移）。调用方在验签之后用
    ``_normalized_issuer_match`` 显式比对一次。
    """
    for attempt in (False, True):
        keys = await _signing_keys_for(issuer, refresh=attempt)
        if keys is None:
            return None
        jwk = keys.get(kid)
        if jwk is None:
            continue
        try:
            claims: dict[str, Any] = jose_jwt.decode(
                token,
                jwk,
                algorithms=[ALGORITHM],
                audience=settings.mcp_resource_url,
            )
        except JWTError as exc:
            # 验签失败 / 过期 / audience 不符 —— 三者都退化成同一个 ``MALFORMED``，
            # 但它们要改的地方完全不同（换密钥轮换、校时钟、改 PUBLIC_BASE_URL）。
            # 原因**只进日志，不进响应**：对客户端说清"为什么没通过"，等于给攻击者
            # 送一份免费的探测反馈（本模块开头的分流说明有同一条理由）。
            logger.warning(
                "as_token_decode_failed",
                kid=kid,
                expected_audience=settings.mcp_resource_url,
                detail=str(exc)[:160],
            )
            return None
        return claims
    return None


def _required_string(claims: dict[str, Any], name: str) -> str | None:
    value = claims.get(name)
    if isinstance(value, str) and value:
        return value
    return None


async def verify_as_access_token(token: str) -> AsAccessClaims | TokenRejection:
    """校验一个 AS 签发的 access token。失败返回 ``TokenRejection``，不抛异常。"""
    try:
        header = jose_jwt.get_unverified_header(token)
    except JWTError:
        return TokenRejection.MALFORMED
    except (TypeError, ValueError):
        return TokenRejection.MALFORMED

    if header.get("alg") != ALGORITHM:
        # 算法由本常量固定，绝不从令牌头里取。见 ALGORITHM 的说明。
        return TokenRejection.MALFORMED
    if header.get("typ") != ACCESS_TOKEN_TYPE:
        return TokenRejection.WRONG_TYPE

    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        return TokenRejection.MALFORMED

    issuer = unverified_issuer(token)
    if issuer is None or not accepts_issuer(issuer):
        return TokenRejection.MALFORMED

    claims = await _decode_signed_claims(token, issuer=issuer, kid=kid)
    if claims is None:
        return TokenRejection.MALFORMED

    # 用**已验证**的那一份再比一次 iss。上面那次比对用的是未验签的值，只能用于分流；
    # 签名覆盖的这一份才是事实。
    verified_issuer = claims.get("iss")
    if not isinstance(verified_issuer, str) or not _normalized_issuer_match(verified_issuer, issuer):
        return TokenRejection.MALFORMED

    user_id = _required_string(claims, "sub")
    tenant_id = _required_string(claims, "tenant_id")
    role = _required_string(claims, "role")
    client_id = _required_string(claims, "client_id")
    token_id = _required_string(claims, "jti")
    family_id = _required_string(claims, "family_id")
    if None in (user_id, tenant_id, role, client_id, token_id, family_id):
        return TokenRejection.INCOMPLETE
    assert user_id and tenant_id and role and client_id and token_id and family_id

    try:
        UUID(family_id)
    except ValueError:
        # family_id 会被用作"安装实例"标识（见 adapters），形态不对就没法映射。
        return TokenRejection.INCOMPLETE

    scopes = parse_scopes(str(claims.get("scope") or ""))

    try:
        revoked, ceiling = await _load_registry_state(
            jti=token_id,
            family_id=family_id,
            client_id=client_id,
            tenant_id=tenant_id,
            user_id=user_id,
            role=role,
            auth_version=int(claims.get("auth_version") or 0),
        )
    except Exception:
        # 校验失败必须 fail-closed。数据库不可用时 /mcp 本来就不可用，但"因为查不到
        # 撤销状态所以放行"是绝不能出现的降级 —— 那正好是撤销功能失效的时刻。
        logger.exception("as_token_registry_lookup_failed", client_id=client_id)
        return TokenRejection.MALFORMED
    if revoked:
        logger.warning("as_token_revoked", client_id=client_id, tenant_id=tenant_id)
        return TokenRejection.REVOKED

    if ceiling is None:
        logger.warning("as_client_not_registered_locally", client_id=client_id, issuer=issuer)
        return TokenRejection.REVOKED

    return AsAccessClaims(
        issuer=issuer,
        client_id=client_id,
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        email=str(claims.get("email") or ""),
        scopes=scopes,
        client_ceiling=ceiling,
        family_id=family_id,
        token_id=token_id,
    )


def _looks_like_an_authorization_server(issuer: str | None) -> bool:
    """令牌自称的 issuer 是否像"某个授权服务器"（绝对 http(s) URL）。

    这个判别器存在的唯一理由是**把噪声关掉**：平台自签令牌的 ``iss`` 是
    ``hrbp-ai-workbench``（不是 URL），它本来就走平台分支、本来就是正常路径。
    如果无条件记日志，每一笔平台令牌流量都会写一行 WARNING，真正的问题反而被刷没。

    它不参与任何信任判断 —— 信任只看 ``accepts_issuer``。这里只是"要不要说一声"。
    """
    if not issuer:
        return False
    parts = urlsplit(issuer)
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


async def resolve_access_claims(token: str) -> AccessClaims | AsAccessClaims | TokenRejection:
    """**本层的唯一凭据入口**：一个 bearer 值 → 已校验的身份，或一个拒绝类别。

    按 ``iss`` 分流。平台自签令牌的 ``iss`` 是 ``hrbp-ai-workbench``（不是 URL），
    永远不可能命中授权服务器信任列表，因此两条路径不会互相吞掉对方的令牌。

    调用方必须显式处理三种返回类型 —— 联合类型而不是 ``Optional``，就是为了让
    "忘了处理拒绝"成为类型检查期可见的问题（与 ``read_access_claims`` 同一约定）。
    """
    issuer = unverified_issuer(token)
    if issuer is not None and accepts_issuer(issuer):
        return await verify_as_access_token(token)

    # 落到这里有两种情况，必须分开看：
    #   ① 平台自签令牌（正常路径，静默）；
    #   ② **一个自称是 AS 的令牌，但那个 issuer 不在信任列表里** —— 它会被退回
    #      平台校验，而平台校验用 HS256 + 平台密钥，**永远验不过** ES256 的 AS 令牌。
    #      最终只留下中间件那一句 ``mcp_token_rejected reason=malformed``。
    #
    # ② 以前是静默的，代价实测很大：``MCP_AUTHORIZATION_SERVERS`` 留空时所有 AS 令牌
    # 都掉进这条分支，而"信任列表为空"这件事在日志里查不到，只能从 ``malformed``
    # 逆推（2026-09-16 花了十几轮排查才定位到一个没配的环境变量）。
    if _looks_like_an_authorization_server(issuer):
        trusted = settings.authorization_servers
        logger.warning(
            "as_token_issuer_not_trusted",
            issuer=(issuer or "")[:200],
            trusted_issuers=list(trusted),
            hint=(
                "MCP_AUTHORIZATION_SERVERS is empty, so every AS-issued token is routed to platform "
                "verification; that path uses an HS256 platform secret and can never validate an ES256 "
                "AS token, so the only visible symptom is 'malformed'."
                if not trusted
                else "the token's issuer is not listed in MCP_AUTHORIZATION_SERVERS"
            ),
        )
    return read_access_claims(token)
