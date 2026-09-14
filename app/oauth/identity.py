"""AS 侧的用户认证：复用平台同一份用户表与同一种校验方式。

刻意**不**另写一套登录逻辑。AS 与平台必须对"这个邮箱 + 密码能不能登录"给出完全
相同的答案，否则会出现一种没人能解释的状态：同一个人能进工作台，却授权不了外部
Agent（或反过来）。所以这里走的是与 ``app/access/routes/auth.py`` 完全相同的路径：
同一个 ``get_db_session``、同一个 ``UserRepository``、同一种 ``bcrypt`` 校验，
连"用户不存在时也照常跑一次 bcrypt"这个防计时侧信道的动作也照搬。
"""

from __future__ import annotations

from app.data.models import oauth as models
from app.oauth.sessions import AsSession
from app.shared.logger import get_logger

logger = get_logger(__name__)

#: 缺省租户（与平台登录共用，定义在 ``app.data.models.oauth``）。AS 登录的租户**不
#: 继承** ``user.tenant_id``：OAuth 令牌由 RS 按 ``tenant_id`` 定位数据，会话租户与
#: RS 校验的租户不一致，令牌拿到的就是另一个（或不存在的）租户的数据，表现成"登录
#: 成功却一直 401"。所以会话租户由**授权请求的客户端**决定（``authorize`` 传入
#: ``client.tenant_id``），多租户部署据此把客户端接到各自的用户目录；单租户部署
#: 什么都不用配，缺省值就是行为（见 ADR-0002 §13.6）。
DEFAULT_TENANT = models.DEFAULT_TENANT

#: 用户不存在时用来消耗等量 CPU 的占位哈希（与 ``app/access/routes/auth.py`` 同一取值）。
#: 没有它，"用户不存在"会比"密码错误"快一个数量级，于是响应时间本身就成了一个
#: "这个邮箱注册过吗"的探测器。
_DUMMY_BCRYPT_HASH = "$2b$12$C6UzMDM.H6dfI/f/IKcEeOe0ZVg2Lz0V5A1E1L1o3d1Wv9C1mQx1W"


async def authenticate(email: str, password: str, *, tenant_id: str = DEFAULT_TENANT) -> AsSession | None:
    """按邮箱与密码认证，并把会话租户**钉死**为调用方指定的租户。失败一律返回 ``None``。

    租户由授权请求的客户端决定（见模块顶部说明），**不是**从查出来的用户记录继承：
    即使 ``users`` 表里这条记录的 ``tenant_id`` 是别的值（数据异常），签出的会话与
    后续令牌也只属于登录时选定的那个租户。

    失败原因（用户不存在 / 密码错误 / 数据库不可用）**不对外区分**：区分它们等于对外
    提供一个账号枚举接口，而区分的信息只对审计有价值 —— 那部分进日志。
    """
    import bcrypt

    supplied = (email or "").strip()
    if not supplied or not password or not tenant_id:
        return None

    from app.data.database import get_db_session
    from app.data.repositories.user_repo import UserRepository

    user = None
    try:
        # 显式以客户端归属的租户进入 RLS 上下文，并让查询**自己**带租户过滤：
        # RLS 是否真正生效取决于部署（角色是否表 owner、是否 FORCE），登录的正确性
        # 不能依赖那个配置 —— 过滤必须发生在查询里（见 UserRepository.get_by_email）。
        async for db in get_db_session(tenant_id):
            repo = UserRepository(db)
            user = await repo.get_by_email(supplied, tenant_id=tenant_id)
    except Exception as exc:
        logger.error("oauth_as_login_db_failed", error=str(exc))
        return None

    if user is None or not user.is_active:
        bcrypt.checkpw(password.encode("utf-8"), _DUMMY_BCRYPT_HASH.encode("utf-8"))
        logger.warning("oauth_as_login_unknown_user")
        return None

    try:
        password_ok = bcrypt.checkpw(password.encode("utf-8"), user.hashed_password.encode("utf-8"))
    except ValueError:
        password_ok = False
    if not password_ok:
        logger.warning("oauth_as_login_bad_password", user_id=user.id)
        return None

    logger.info("oauth_as_login_success", user_id=user.id, role=user.role)
    return AsSession(
        user_id=user.id,
        # 钉死为登录选定的租户：不继承 user.tenant_id，避免跨租户令牌（见模块顶部说明）。
        tenant_id=tenant_id,
        role=user.role,
        email=user.email,
        name=user.name,
        auth_version=user.auth_version,
    )


async def current_identity(user_id: str, tenant_id: str) -> AsSession | None:
    """Return the current active identity for a previously authenticated user.

    Every OAuth continuation calls this instead of trusting role/status copied
    into a cookie, code, or refresh token.
    """
    if not user_id or not tenant_id:
        return None
    from sqlalchemy import select

    from app.data.database import get_db_session
    from app.data.models.user import User

    try:
        user = None
        async for db in get_db_session(tenant_id):
            user = await db.scalar(select(User).where(User.id == user_id, User.tenant_id == tenant_id))
    except Exception as exc:
        logger.error("oauth_identity_revalidation_failed", user_id=user_id, tenant_id=tenant_id, error=str(exc))
        return None
    if user is None or not user.is_active:
        return None
    return AsSession(
        user_id=user.id,
        tenant_id=user.tenant_id,
        role=user.role,
        email=user.email,
        name=user.name,
        auth_version=user.auth_version,
    )


async def revalidate_session(session: AsSession) -> AsSession | None:
    """Reject stale browser sessions after role, password, or status changes."""
    current = await current_identity(session.user_id, session.tenant_id)
    if current is None or current.auth_version != session.auth_version or current.role != session.role:
        return None
    return current
