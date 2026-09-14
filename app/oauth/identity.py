"""AS 侧的用户认证：复用平台同一份用户表与同一种校验方式。

刻意**不**另写一套登录逻辑。AS 与平台必须对"这个邮箱 + 密码能不能登录"给出完全
相同的答案，否则会出现一种没人能解释的状态：同一个人能进工作台，却授权不了外部
Agent（或反过来）。所以这里走的是与 ``app/access/routes/auth.py`` 完全相同的路径：
同一个 ``get_db_session``、同一个 ``UserRepository``、同一种 ``bcrypt`` 校验，
连"用户不存在时也照常跑一次 bcrypt"这个防计时侧信道的动作也照搬。
"""

from __future__ import annotations

from app.oauth.sessions import AsSession
from app.shared.logger import get_logger

logger = get_logger(__name__)

#: AS 登录**固定**使用默认租户。外部 Agent 接入走的是单租户部署：OAuth 令牌签发后由
#: RS 按 ``tenant_id`` 定位数据，一旦 AS 会话租户与 RS 校验的租户不一致，令牌拿到的就是
#: 另一个（或不存在的）租户的数据，表现成"登录成功却一直 401"。所以这里**不继承**
#: ``user.tenant_id``，而是把会话租户**钉死**为 ``DEFAULT_TENANT``。多租户部署要接外部
#: Agent 时，这一条必须先解（需要按请求/租户路由选择登录库，见 ADR-0002 §13.6）。
DEFAULT_TENANT = "default"

#: 用户不存在时用来消耗等量 CPU 的占位哈希（与 ``app/access/routes/auth.py`` 同一取值）。
#: 没有它，"用户不存在"会比"密码错误"快一个数量级，于是响应时间本身就成了一个
#: "这个邮箱注册过吗"的探测器。
_DUMMY_BCRYPT_HASH = "$2b$12$C6UzMDM.H6dfI/f/IKcEeOe0ZVg2Lz0V5A1E1L1o3d1Wv9C1mQx1W"


async def authenticate(email: str, password: str) -> AsSession | None:
    """按邮箱与密码认证。失败一律返回 ``None``。

    失败原因（用户不存在 / 密码错误 / 数据库不可用）**不对外区分**：区分它们等于对外
    提供一个账号枚举接口，而区分的信息只对审计有价值 —— 那部分进日志。
    """
    import bcrypt

    supplied = (email or "").strip()
    if not supplied or not password:
        return None

    from app.data.database import get_db_session
    from app.data.repositories.user_repo import UserRepository

    user = None
    try:
        # 显式以默认租户进入 RLS 上下文：AS 登录只可见 ``tenant_id == "default"`` 的用户。
        async for db in get_db_session(DEFAULT_TENANT):
            repo = UserRepository(db)
            user = await repo.get_by_email(supplied)
    except Exception as exc:
        logger.error("oauth_as_login_db_failed", error=str(exc))
        return None

    if user is None:
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
        # 钉死为默认租户：不继承 user.tenant_id，避免跨租户令牌（见模块顶部说明）。
        tenant_id=DEFAULT_TENANT,
        role=user.role,
        email=user.email,
        name=user.name,
    )
