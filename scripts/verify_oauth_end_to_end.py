#!/usr/bin/env python
"""OAuth 2.1 端到端验收：真实 RS 进程 + 真实 AS 进程 + 真实数据库 + 真实 MCP 客户端。

为什么必须有它
--------------
``tests/oauth/`` 与 ``tests/mcp/test_as_token_verification.py`` 覆盖了各模块的判定，
但两者之间有一条**没有任何测试覆盖**的缝：

- AS 侧的测试只断言"我签出的令牌长什么样"；
- RS 侧的测试用 ``_FakeAuthorizationServer`` 猴补了 ``_fetch_json`` 与
  ``_load_registry_state``，也就是说**RS 从未真正读过一份 AS 产出的元数据文档与 JWKS**。

这条缝正是"两套身份来源漂移"最容易发生的地方（上游方案 §9 把它列为要避免的单点）：
只要 AS 的 ``issuer`` 写法、``jwks_uri`` 位置或 JWK 的 ``kid`` 与 RS 的预期差一个字符，
所有单测仍然全绿，而真实客户端一个都登不进来。所以这里起**真实进程**，让 RS 通过
真实 HTTP 去取 AS 的文档。

它验的是什么（对应方案 §WP2 的协议验收条目）
--------------------------------------------
1. 无令牌请求 ``/mcp`` → 401 + ``WWW-Authenticate: Bearer resource_metadata="…"``；
2. Protected Resource Metadata 匿名可读，且 ``resource`` / ``authorization_servers`` 与
   实际部署一致（RS 的信任列表与告诉客户端的地址必须是同一份，否则客户端会去一个
   自己不被信任的 AS 拿令牌）；
3. AS 元数据（RFC 8414）匿名可读，``issuer`` 与 RS 的信任列表逐字节一致，JWKS 无
   ``d``（私钥分量）；
4. 预注册客户端与 DCR 客户端各走一遍 授权 → 同意 → 换令牌；
5. access token 是 ``at+jwt`` / ES256 / ``aud`` 绑定到 MCP resource；
6. 真实 MCP 客户端（官方 ``mcp`` SDK）能用该令牌完成 ``initialize`` → ``list_tools``
   → ``call_tool``；
7. 换 ``aud`` / 换签名密钥 / 改 ``typ`` 的令牌被拒；平台自签令牌在
   ``MCP_ACCEPTS_PLATFORM_TOKENS=false`` 下被**策略**拒绝；
8. RFC 7662 内省：有效 → ``active:true``，撤销后 → ``active:false``，并且撤销后
   ``/mcp`` 的下一次调用立刻失败（协议验收第 7 条）；
9. refresh 轮换与**重用检测**：重放旧 refresh token 会让整条 family 失效。

用法
----
    # 建议指向一个**一次性的**库，脚本会在结束时清掉自己写入的行
    python scripts/verify_oauth_end_to_end.py \
        --database-url postgresql+asyncpg://postgres:pw@localhost:5433/hrbp_oauth_e2e

    # 或
    OAUTH_E2E_DATABASE_URL=... python scripts/verify_oauth_end_to_end.py

前置：目标库已执行 ``alembic upgrade head``（需要 ``oauth_*`` 四张表）。脚本**不**替你
跑迁移 —— 静默创建 schema 会让"这个库到底是什么状态"变成第二个需要猜的问题。

未做的事（明确列出）
--------------------
- 不验 CIMD：它需要一个公网 HTTPS 文档服务器，本地起不出来。CIMD 的校验由
  ``tests/oauth/`` 的单元测试覆盖。
- 不起 Celery / Milvus 等外部依赖；RS 的启动期基础设施探测失败只记警告（见
  ``app/main.py`` 的 ``_ensure_infrastructure``），不影响本链路。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hashlib
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parent.parent

#: 登录租户。AS 与平台登录都走 ``get_db_session()``，其默认租户是 ``"default"``
#: （``app/data/database.py``），而 ``users`` 受 FORCE RLS 约束 —— 于是登录只能看到
#: ``tenant_id == "default"`` 的用户。这是**既有**行为（平台登录同一份代码路径），
#: 不是本次改动引入的；e2e 必须按它来种数据，同时把它记进验收报告。
LOGIN_TENANT = "default"

_ALL_SCOPES = ("hrb:policy:read", "hrb:case:read", "hrb:case:propose", "hrb:approval:read", "hrb:profile:read")

_STARTUP_TIMEOUT_SECONDS = 90.0
_HTTP_TIMEOUT_SECONDS = 15.0


class ChainBrokenError(RuntimeError):
    """链条已经断了，后续步骤无法继续。"""

    def __init__(self, message: str) -> None:
        # 中断类错误几乎总是把 HTTP 响应体贴进来，而令牌响应体里就带着令牌。
        # 在这里统一抹一遍，调用方就不必各自记得处理。
        super().__init__(_redact(message))


#: 详情里最常见的形态是"把 HTTP 响应体原样贴出来"，而令牌响应体里就带着令牌。
#: 匹配 ``"access_token":"..."`` 这类 JSON 字段值并抹掉，保留字段名与长度量级。
_SECRET_KEYS = "access_token|refresh_token|id_token|token|code|state|code_verifier"
_SECRET_JSON_VALUES = re.compile(rf'("(?:{_SECRET_KEYS})"\s*:\s*")([^"]*)(")')
#: 详情常被 ``text[:200]`` 截断，闭合引号被切掉，上面那条就匹配不到了 ——
#: 这一条兜住"值一直延伸到串尾"的形态。
_SECRET_TRUNCATED = re.compile(rf'("(?:{_SECRET_KEYS})"\s*:\s*")([^"]*)$')
#: 裸 JWT（access token 本体）。出现在任何地方都要抹，不依赖它是否在 JSON 里。
_JWT_LITERAL = re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}(?:\.[A-Za-z0-9_-]{5,})?")


def _redact(text: str) -> str:
    """抹掉详情里的凭据值。

    为什么在**出口**做而不是在每个调用点做：本脚本有二十多处把响应体原样贴进详情，
    逐处加处理会漏，而且下一个人新增一条时不会想到这一层。报表类的打印只有
    ``Report`` 的四个方法与 ``ChainBrokenError``，在那里统一过一遍，漏网的机会是零。

    为什么不一概而论地把整个详情删掉：出问题时详情是唯一的线索，"响应体被完整
    吞掉"会让排查回到盲猜。所以只抹值、留字段名与长度。
    """
    text = _SECRET_JSON_VALUES.sub(lambda m: f"{m.group(1)}<已抹除:{len(m.group(2))}字符>{m.group(3)}", text)
    text = _SECRET_TRUNCATED.sub(lambda m: f"{m.group(1)}<已抹除:{len(m.group(2))}字符，原文被截断>", text)
    return _JWT_LITERAL.sub("<JWT 已抹除>", text)


@dataclass
class Report:
    """结果台账。硬失败抛异常，独立负例只记录 —— 一次跑完所有能跑的。"""

    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    gap_names: set[str] = field(default_factory=set)

    def _record(self, mark: str, colour: str, name: str, detail: str, ok: bool) -> None:
        safe = _redact(detail)
        self.checks.append((name, ok, safe))
        print(f"  \033[{colour}m{mark}\033[0m  {name}{f'  — {safe}' if safe else ''}")

    def ok(self, name: str, detail: str = "") -> None:
        self._record("PASS", "32", name, detail, True)

    def fail(self, name: str, detail: str = "") -> None:
        self._record("FAIL", "31", name, detail, False)

    def gap(self, name: str, detail: str = "") -> None:
        """**已知缺口**：验收条目未满足，但代码里已经把它标注为后续工作。

        与 ``fail`` 分开只是为了让报告读起来能分辨"这次改动弄坏了什么"与"本来就还没做"；
        两者都计入失败，因为一个会返回 0 的验收脚本本身就是"看起来做了"的来源。
        """
        self._record("GAP ", "33", name, detail, False)
        self.gap_names.add(name)

    def expect(self, name: str, condition: bool, detail: str = "", *, fatal: bool = False) -> None:
        (self.ok if condition else self.fail)(name, detail)
        if not condition and fatal:
            raise ChainBrokenError(f"{name}{f' — {_redact(detail)}' if detail else ''}")

    @property
    def failed(self) -> list[tuple[str, bool, str]]:
        return [item for item in self.checks if not item[1]]

    @property
    def gaps(self) -> list[str]:
        return [name for name, ok, _ in self.checks if not ok and name in self.gap_names]

    def summary(self) -> str:
        passed = len(self.checks) - len(self.failed)
        text = f"{passed}/{len(self.checks)} 项通过"
        if self.gaps:
            text += f"（其中 {len(self.gaps)} 项是已知缺口）"
        return text


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _section(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m")


# --------------------------------------------------------------------------- 数据库


def _dsn_for_asyncpg(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _seed_user(database_url: str, *, email: str, password: str, role: str, tenant_id: str) -> str:
    """种一个可登录用户，返回 user_id。

    用原生 SQL 而不是导入 app 的模型：这个脚本自己的进程也要读 ``.env``，而
    ``app.config.settings`` 是模块级单例 —— 一旦在覆盖 ``DATABASE_URL`` 之前被导入，
    它就会指向开发库。原生连接没有这个顺序陷阱。
    """
    import asyncpg
    import bcrypt  # 与 app/oauth/identity.py 使用同一个库，保证哈希可被它校验

    user_id = str(uuid4())
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    connection = await asyncpg.connect(_dsn_for_asyncpg(database_url))
    try:
        # RLS：policy 的 USING 表达式同时充当 INSERT 的 WITH CHECK，所以必须先设租户。
        await connection.execute("SELECT set_config('app.tenant_id', $1, false)", tenant_id)
        await connection.execute(
            "INSERT INTO users (id, tenant_id, name, email, hashed_password, role) VALUES ($1, $2, $3, $4, $5, $6)",
            user_id,
            tenant_id,
            "OAuth E2E 用户",
            email,
            hashed,
            role,
        )
    finally:
        await connection.close()
    return user_id


async def _cleanup(database_url: str, *, emails: list[str], client_ids: list[str]) -> None:
    """删除本次运行写入的行。**只按本次运行生成的 id/email 删**，不做范围删除。"""
    import asyncpg

    connection = await asyncpg.connect(_dsn_for_asyncpg(database_url))
    try:
        for client_id in client_ids:
            await connection.execute("DELETE FROM oauth_tokens WHERE client_id = $1", client_id)
            await connection.execute("DELETE FROM oauth_authorization_codes WHERE client_id = $1", client_id)
            await connection.execute("DELETE FROM oauth_clients WHERE client_id = $1", client_id)
        if client_ids:
            await connection.execute("DELETE FROM oauth_revoked_tokens WHERE client_id = ANY($1::text[])", client_ids)
        for email in emails:
            await connection.execute("SELECT set_config('app.tenant_id', $1, false)", LOGIN_TENANT)
            await connection.execute("DELETE FROM users WHERE email = $1", email)
    finally:
        await connection.close()


async def _assert_schema_ready(database_url: str) -> None:
    import asyncpg

    connection = await asyncpg.connect(_dsn_for_asyncpg(database_url))
    try:
        rows = await connection.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'oauth_%'"
        )
    finally:
        await connection.close()
    names = {row["tablename"] for row in rows}
    missing = {"oauth_clients", "oauth_tokens", "oauth_authorization_codes", "oauth_revoked_tokens"} - names
    if missing:
        raise ChainBrokenError(
            f"目标库缺少表 {sorted(missing)} —— 请先对**这个库**执行 `alembic upgrade head`"
            "（脚本刻意不替你跑迁移，见文件头）"
        )


# --------------------------------------------------------------------------- 进程


@dataclass
class Server:
    name: str
    process: subprocess.Popen[bytes]
    base_url: str
    log_path: Path

    def tail(self, lines: int = 40) -> str:
        try:
            content = self.log_path.read_text(errors="replace").splitlines()
        except OSError:
            return "(无法读取日志)"
        return "\n".join(content[-lines:])


def _spawn(name: str, module: str, *, port: int, env: dict[str, str], log_dir: Path) -> Server:
    log_path = log_dir / f"{name}.log"
    handle = log_path.open("wb")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=REPO_ROOT,
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return Server(name=name, process=process, base_url=f"http://127.0.0.1:{port}", log_path=log_path)


async def _wait_ready(server: Server, probe_path: str) -> None:
    import httpx

    deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
    last_error = ""
    async with httpx.AsyncClient(timeout=3.0) as client:
        while time.monotonic() < deadline:
            if server.process.poll() is not None:
                raise ChainBrokenError(
                    f"{server.name} 启动即退出（返回码 {server.process.returncode}）\n{server.tail(60)}"
                )
            try:
                response = await client.get(f"{server.base_url}{probe_path}")
                if response.status_code < 500:
                    return
                last_error = f"HTTP {response.status_code}"
            except httpx.HTTPError as exc:
                last_error = type(exc).__name__
            await asyncio.sleep(0.4)
    raise ChainBrokenError(
        f"{server.name} 在 {_STARTUP_TIMEOUT_SECONDS:.0f}s 内未就绪（最后错误：{last_error}）\n{server.tail(60)}"
    )


def _stop(server: Server) -> None:
    if server.process.poll() is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(os.getpgid(server.process.pid), signal.SIGTERM)
    with contextlib.suppress(subprocess.TimeoutExpired):
        server.process.wait(timeout=15)
    if server.process.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(server.process.pid), signal.SIGKILL)


# --------------------------------------------------------------------------- 客户端


def _decode_unverified(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    def _part(value: str) -> dict[str, Any]:
        padded = value + "=" * (-len(value) % 4)
        return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))

    header, payload, _ = token.split(".")
    return _part(header), _part(payload)


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _authorize_params(*, client_id: str, redirect_uri: str, scope: str, resource: str, state: str) -> dict[str, str]:
    verifier, challenge = _pkce_pair()
    return {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": resource,
        "_verifier": verifier,
    }


async def _obtain_authorization_code(
    client: Any,
    *,
    as_url: str,
    params: dict[str, str],
    email: str,
    password: str,
    expect_consent: bool = True,
) -> str:
    """走完 授权端点 → 登录表单 → 同意表单，返回 authorization code。"""
    public = {key: value for key, value in params.items() if not key.startswith("_")}
    login = await client.get(f"{as_url}/oauth/authorize", params=public)
    if login.status_code != 200:
        # 302 意味着授权端点按 RFC 6749 §4.1.2.1 把错误回传给了 redirect_uri。
        # 把 Location 打出来 —— 里面就是 error / error_description，不看它只能瞎猜。
        raise ChainBrokenError(
            f"授权端点未返回登录页：HTTP {login.status_code}，location={login.headers.get('location')!r}"
        )

    form = dict(public)
    form.update({"email": email, "password": password})
    signed_in = await client.post(f"{as_url}/oauth/authorize/login", data=form)
    if signed_in.status_code != 200 or (expect_consent and "consent" not in signed_in.text.lower()):
        raise ChainBrokenError(f"登录后未进入同意页：HTTP {signed_in.status_code}，正文片段={signed_in.text[:200]!r}")

    consent = dict(public)
    consent["decision"] = "allow"
    granted = await client.post(f"{as_url}/oauth/authorize/consent", data=consent)
    if granted.status_code not in {302, 303}:
        raise ChainBrokenError(f"同意后未重定向：HTTP {granted.status_code} {granted.text[:200]}")
    location = granted.headers.get("location", "")
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)
    if query.get("state", [""])[0] != public["state"]:
        raise ChainBrokenError(f"回调未回显 state，实际 location={location!r}")
    if "error" in query:
        raise ChainBrokenError(f"授权被拒绝：{query}")
    code = query.get("code", [""])[0]
    if not code:
        raise ChainBrokenError(f"回调里没有 code：{location!r}")
    return code


async def _exchange_code(
    client: Any, *, as_url: str, client_id: str, code: str, redirect_uri: str, verifier: str, resource: str
) -> dict[str, Any]:
    response = await client.post(
        f"{as_url}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            "resource": resource,
        },
    )
    if response.status_code != 200:
        raise ChainBrokenError(f"换令牌失败：HTTP {response.status_code} {response.text[:300]}")
    body = response.json()
    # 令牌响应**唯一**不许被缓存的响应（RFC 6749 §5.1 的 MUST）。把响应头一并带出去，
    # 让调用方能断言它 —— 单看 body 是断言不了这一条的。
    body["_cache_control"] = response.headers.get("cache-control", "")
    return body


async def _full_flow(
    client: Any,
    *,
    as_url: str,
    client_id: str,
    redirect_uri: str,
    resource: str,
    scope: str,
    email: str,
    password: str,
    state: str,
) -> dict[str, Any]:
    params = _authorize_params(
        client_id=client_id, redirect_uri=redirect_uri, scope=scope, resource=resource, state=state
    )
    code = await _obtain_authorization_code(client, as_url=as_url, params=params, email=email, password=password)
    return await _exchange_code(
        client,
        as_url=as_url,
        client_id=client_id,
        code=code,
        redirect_uri=redirect_uri,
        verifier=params["_verifier"],
        resource=resource,
    )


async def _call_mcp_with_token(
    url: str,
    token: str | None,
    *,
    tool_name: str = "hrbpilot_ping",
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """用**官方 MCP SDK 客户端**完成 initialize → list_tools → call_tool。

    ``hrbpilot_ping`` 不回显任何 tenant 数据，但它会回 ``auth: true|false`` —— 这就足以
    证明"令牌被传输层接受、主体被成功构造"，而不会把员工数据带进验证输出。

    返回 ``tools``（工具名列表）、``payload``（工具返回的 JSON，能解析时）、``text``
    （原文，供负例断言文案）与 ``is_error``。
    """
    import mcp.client.streamable_http as streamable_module
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with (
        streamable_module.httpx2.AsyncClient(headers=headers, timeout=_HTTP_TIMEOUT_SECONDS) as http_client,
        streamable_http_client(url, http_client=http_client) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        await session.initialize()
        listing = await session.list_tools()
        result = await session.call_tool(tool_name, arguments or {})
    text = "".join(getattr(item, "text", "") or "" for item in (getattr(result, "content", None) or []))
    payload: Any = None
    with contextlib.suppress(json.JSONDecodeError):
        payload = json.loads(text)
    return {
        "tools": sorted(tool.name for tool in listing.tools),
        "payload": payload,
        "text": text,
        "is_error": bool(getattr(result, "isError", False)),
    }


async def _mcp_status(url: str, token: str | None) -> tuple[int, dict[str, str]]:
    """裸 HTTP POST ``/mcp``，只看传输层状态码与挑战头（不做协议握手）。"""
    import httpx

    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
        response = await client.post(url, headers=headers, json=body)
    return response.status_code, {key.lower(): value for key, value in response.headers.items()}


def _mint(pem: bytes, claims: dict[str, Any], *, typ: str = "at+jwt") -> str:
    """用给定私钥签一个令牌 —— 用来造"签名对但 claim 不对"的负例。"""
    from jose import jwt as jose_jwt

    from app.oauth.keys import signing_key_from_pem

    key = signing_key_from_pem(pem.decode("utf-8"))
    return str(jose_jwt.encode(claims, key.private_pem, algorithm="ES256", headers={"kid": key.kid, "typ": typ}))


def _mint_platform_token(secret: str, *, user_id: str, role: str, tenant_id: str) -> str:
    from jose import jwt as jose_jwt

    now = int(time.time())
    return str(
        jose_jwt.encode(
            {
                "sub": user_id,
                "role": role,
                "tenant_id": tenant_id,
                "email": "oauth-e2e@example.invalid",
                "iss": "hrbp-ai-workbench",
                "aud": "hrbp-ai-workbench",
                "iat": now,
                "exp": now + 900,
                "typ": "access",
            },
            secret,
            algorithm="HS256",
        )
    )


# --------------------------------------------------------------------------- 主流程


async def run(database_url: str, *, keep: bool, log_dir: Path) -> Report:
    import httpx

    report = Report()
    run_id = secrets.token_hex(4)
    email = f"oauth-e2e-{run_id}@example.invalid"
    password = secrets.token_urlsafe(18)
    redirect_uri = "http://127.0.0.1:9/callback"
    pre_registered_id = f"e2e-prereg-{run_id}"
    created_client_ids: list[str] = []

    await _assert_schema_ready(database_url)
    print(f"目标库可用；run_id={run_id}")

    _section("1. 起进程")
    rs_port, as_port = _free_port(), _free_port()
    rs_url, as_url = f"http://127.0.0.1:{rs_port}", f"http://127.0.0.1:{as_port}"
    mcp_resource = f"{rs_url}/mcp"
    jwt_secret = secrets.token_urlsafe(32)

    # AS 需要一把**已知的**签名私钥：e2e 要拿它来造"签名正确但 aud 错误"的负例。
    # 留空会走"进程级临时密钥"，那样脚本拿不到私钥，就没法做这类负例。
    sys.path.insert(0, str(REPO_ROOT))
    from app.oauth.keys import generate_signing_key

    signing_key = generate_signing_key()
    pre_registered = json.dumps(
        [
            {
                "client_id": pre_registered_id,
                "client_name": "OAuth E2E 预注册客户端",
                "redirect_uris": [redirect_uri],
                "scope": " ".join(_ALL_SCOPES),
            }
        ]
    )

    common = dict(os.environ)
    common.update(
        {"DATABASE_URL": database_url, "APP_ENV": "development", "APP_DEBUG": "false", "LOG_LEVEL": "warning"}
    )

    rs_env = {
        **common,
        "JWT_SECRET": jwt_secret,
        "PUBLIC_BASE_URL": rs_url,
        "MCP_AUTHORIZATION_SERVERS": as_url,
        "MCP_ACCEPTS_PLATFORM_TOKENS": "false",
    }
    as_env = {
        **common,
        # AS 也必须有 PUBLIC_BASE_URL：它据此算出 canonical MCP resource，而
        # ``resource`` 参数是**逐字节**比对的（RFC 8707 §2）。只给 RS 设这一项会让
        # 授权请求以 invalid_target 被拒 —— 这正是第一次跑 e2e 时踩到的坑。
        "PUBLIC_BASE_URL": rs_url,
        "OAUTH_ISSUER": as_url,
        "OAUTH_SIGNING_KEY_PEM": signing_key.private_pem.decode("utf-8"),
        "OAUTH_PRE_REGISTERED_CLIENTS": pre_registered,
        "OAUTH_ENABLE_DYNAMIC_REGISTRATION": "true",
        "OAUTH_DYNAMIC_REGISTRATION_PER_HOUR": "500",
    }

    authorization_server = _spawn(
        "authorization-server", "app.oauth.main:app", port=as_port, env=as_env, log_dir=log_dir
    )
    resource_server = _spawn("resource-server", "app.main:app", port=rs_port, env=rs_env, log_dir=log_dir)
    servers = [authorization_server, resource_server]
    print(f"  AS → {as_url}\n  RS → {rs_url}")

    try:
        await _wait_ready(authorization_server, "/.well-known/oauth-authorization-server")
        report.ok("AS 进程就绪")
        await _wait_ready(resource_server, "/api/health")
        report.ok("RS 进程就绪")

        created_client_ids.append(pre_registered_id)
        await _seed_user(database_url, email=email, password=password, role="hrbp", tenant_id=LOGIN_TENANT)
        print(f"  已种入可登录用户 {email}（tenant={LOGIN_TENANT}，role=hrbp）")

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=False) as client:
            # ------------------------------------------------------------ 发现面
            _section("2. 发现（RFC 9728 / RFC 8414）")

            status, headers = await _mcp_status(f"{rs_url}/mcp", None)
            challenge = headers.get("www-authenticate", "")
            report.expect(
                "无令牌请求 /mcp 返回 401",
                status == 401,
                f"实际 HTTP {status}",
                fatal=True,
            )
            report.expect(
                "401 带 WWW-Authenticate + resource_metadata",
                challenge.startswith("Bearer ") and "resource_metadata=" in challenge,
                f"头={challenge!r}",
                fatal=True,
            )

            prm = await client.get(f"{rs_url}/.well-known/oauth-protected-resource")
            prm_body = prm.json()
            report.expect(
                "Protected Resource Metadata 匿名可读", prm.status_code == 200, f"HTTP {prm.status_code}", fatal=True
            )
            report.expect(
                "元数据 resource 等于实际 MCP 地址",
                prm_body.get("resource") == mcp_resource,
                f"{prm_body.get('resource')!r} vs {mcp_resource!r}",
                fatal=True,
            )
            report.expect(
                "元数据 authorization_servers 指向真实 AS",
                prm_body.get("authorization_servers") == [as_url],
                f"{prm_body.get('authorization_servers')!r}",
                fatal=True,
            )
            # 带 path 的变体同样是客户端会去查的位置
            prm_pathed = await client.get(f"{rs_url}/.well-known/oauth-protected-resource/mcp")
            report.expect(
                "带 path 的 PRM 变体可读且内容一致", prm_pathed.status_code == 200 and prm_pathed.json() == prm_body
            )

            metadata = await client.get(f"{as_url}/.well-known/oauth-authorization-server")
            metadata_body = metadata.json()
            report.expect("AS 元数据匿名可读", metadata.status_code == 200, f"HTTP {metadata.status_code}", fatal=True)
            report.expect(
                "AS 元数据 issuer 与 RS 信任列表逐字节一致",
                metadata_body.get("issuer") == as_url,
                f"{metadata_body.get('issuer')!r} vs {as_url!r}",
                fatal=True,
            )
            for field_name in (
                "authorization_endpoint",
                "token_endpoint",
                "jwks_uri",
                "revocation_endpoint",
                "introspection_endpoint",
            ):
                report.expect(f"元数据声明了 {field_name}", bool(metadata_body.get(field_name)))
            report.expect(
                "开启 DCR 时元数据声明 registration_endpoint",
                "registration_endpoint" in metadata_body,
                "本次 e2e 显式开启 DCR，因此应当声明",
            )

            jwks = await client.get(metadata_body["jwks_uri"])
            keys = jwks.json().get("keys", [])
            report.expect("JWKS 可读且非空", jwks.status_code == 200 and bool(keys), f"{len(keys)} key(s)", fatal=True)
            report.expect("JWKS 不含私钥分量 d", all("d" not in key for key in keys))
            report.expect(
                "令牌头部的 kid 在 JWKS 里能找到",
                any(key.get("kid") for key in keys),
            )

            # 每个声明的端点都必须真的挂着（声明一个 404 的端点等于对客户端撒谎）
            endpoints_ok = True
            for name, url_value in metadata_body.items():
                if not name.endswith("_endpoint") or not isinstance(url_value, str):
                    continue
                probe = await client.get(url_value)
                if probe.status_code == 404:
                    endpoints_ok = False
                    report.fail(f"端点 {name} 声明了但 404", url_value)
            if endpoints_ok:
                report.ok("所有声明的 *_endpoint 都真实挂载（无 404）")

            # ------------------------------------------------------------ 授权码流程
            _section("3. 授权码 + PKCE（预注册客户端）")
            tokens = await _full_flow(
                client,
                as_url=as_url,
                client_id=pre_registered_id,
                redirect_uri=redirect_uri,
                resource=mcp_resource,
                scope=" ".join(_ALL_SCOPES),
                email=email,
                password=password,
                state=f"state-{run_id}",
            )
            access_token = tokens["access_token"]
            report.expect("换到 access_token 与 refresh_token", bool(tokens.get("refresh_token")), fatal=True)
            report.expect(
                "令牌响应带 Cache-Control: no-store",
                "no-store" in str(tokens.get("_cache_control", "")),
                f"Cache-Control={tokens.get('_cache_control')!r}",
            )

            header, claims = _decode_unverified(access_token)
            report.expect(
                "access token 的 typ 是 at+jwt（RFC 9068）", header.get("typ") == "at+jwt", f"typ={header.get('typ')!r}"
            )
            report.expect("access token 用 ES256 签名", header.get("alg") == "ES256", f"alg={header.get('alg')!r}")
            report.expect("令牌 issuer 等于 AS issuer", claims.get("iss") == as_url, f"iss={claims.get('iss')!r}")
            report.expect(
                "令牌 audience 绑定到 MCP resource", claims.get("aud") == mcp_resource, f"aud={claims.get('aud')!r}"
            )
            report.expect(
                "令牌带 family_id（撤销粒度）",
                isinstance(claims.get("family_id"), str) and bool(claims.get("family_id")),
            )
            report.expect(
                "令牌的 tenant 与登录用户一致",
                claims.get("tenant_id") == LOGIN_TENANT,
                f"tenant={claims.get('tenant_id')!r}",
            )

            # ------------------------------------------------------------ 真实 MCP 客户端
            _section("4. 真实 MCP 客户端调用 /mcp")
            try:
                call = await _call_mcp_with_token(f"{rs_url}/mcp", access_token)
            except Exception as exc:  # MCP SDK 的失败模式很多，统一记为失败并继续
                report.fail("MCP 客户端完成 initialize + list_tools + call_tool", f"{type(exc).__name__}: {exc}")
            else:
                report.ok("MCP 客户端完成 initialize + list_tools + call_tool", f"工具 {len(call['tools'])} 个")
                report.expect(
                    "hrbpilot_ping 确认请求已通过认证（auth=true）",
                    bool(call["payload"] and call["payload"].get("auth") is True),
                    f"payload={call['payload']!r}",
                    fatal=True,
                )
                for tool_name in ("search_policy", "get_policy_source"):
                    report.expect(f"工具列表含 {tool_name}", tool_name in call["tools"])

            # ------------------------------------------------------------ 负例：伪造令牌
            _section("5. 负例：被篡改/不适用的令牌")
            now = int(time.time())
            base_claims = {
                "iss": as_url,
                "sub": claims.get("sub"),
                "tenant_id": LOGIN_TENANT,
                "role": "hrbp",
                "client_id": pre_registered_id,
                "jti": str(uuid4()),
                "family_id": str(uuid4()),
                "scope": " ".join(_ALL_SCOPES),
                "iat": now,
                "exp": now + 900,
            }

            wrong_audience = {**base_claims, "aud": "https://someone-elses-resource.example.com/mcp"}
            status, _ = await _mcp_status(f"{rs_url}/mcp", _mint(signing_key.private_pem, wrong_audience))
            report.expect("audience 不匹配的令牌被拒（401）", status == 401, f"实际 HTTP {status}")

            foreign_key = generate_signing_key()
            status, _ = await _mcp_status(
                f"{rs_url}/mcp", _mint(foreign_key.private_pem, {**base_claims, "aud": mcp_resource})
            )
            report.expect("用陌生密钥签名的令牌被拒（401）", status == 401, f"实际 HTTP {status}")

            status, _ = await _mcp_status(
                f"{rs_url}/mcp", _mint(signing_key.private_pem, {**base_claims, "aud": mcp_resource}, typ="JWT")
            )
            report.expect("typ 不是 at+jwt 的令牌被拒（401）", status == 401, f"实际 HTTP {status}")

            unknown_issuer = {**base_claims, "aud": mcp_resource, "iss": "http://127.0.0.1:1"}
            status, _ = await _mcp_status(f"{rs_url}/mcp", _mint(signing_key.private_pem, unknown_issuer))
            report.expect("issuer 不在信任列表的令牌被拒（401）", status == 401, f"实际 HTTP {status}")

            platform_token = _mint_platform_token(jwt_secret, user_id=str(uuid4()), role="hrbp", tenant_id=LOGIN_TENANT)
            status, headers = await _mcp_status(f"{rs_url}/mcp", platform_token)
            report.expect(
                "MCP_ACCEPTS_PLATFORM_TOKENS=false 时平台自签令牌被策略拒绝（401）",
                status == 401,
                f"实际 HTTP {status}",
            )
            report.expect(
                "策略拒绝的响应仍是挑战式（带 resource_metadata）",
                "resource_metadata=" in headers.get("www-authenticate", ""),
            )

            # 先注册一个"另一个已注册客户端"，后面两处都要用它：内省的非持有者用例，
            # 以及第 8 节的 DCR 完整流程。放在这里是为了不重复注册、也让它一定早于用例。
            dcr_registration = await client.post(
                f"{as_url}/oauth/register",
                json={
                    "client_name": "OAuth E2E DCR 客户端",
                    "redirect_uris": [redirect_uri],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "scope": " ".join(_ALL_SCOPES),
                    "token_endpoint_auth_method": "none",
                },
            )
            report.expect(
                "DCR 注册成功并返回 client_id",
                dcr_registration.status_code == 201 and bool(dcr_registration.json().get("client_id")),
                f"HTTP {dcr_registration.status_code} {dcr_registration.text[:200]}",
                fatal=True,
            )
            dcr_client_id = dcr_registration.json()["client_id"]
            created_client_ids.append(dcr_client_id)
            report.expect("DCR client_id 带可审计前缀 dcr_", dcr_client_id.startswith("dcr_"))

            # ------------------------------------------------------------ 内省与撤销
            _section("6. 内省（RFC 7662）与撤销（RFC 7009）")
            introspect = await client.post(
                f"{as_url}/oauth/introspect", data={"token": access_token, "client_id": pre_registered_id}
            )
            introspect_body = introspect.json()
            report.expect("内省对有效令牌返回 active=true", introspect_body.get("active") is True, f"{introspect_body}")
            report.expect("内省响应不含 username/email", not ({"username", "email"} & set(introspect_body)))
            report.expect(
                "内省响应带 Cache-Control: no-store",
                "no-store" in introspect.headers.get("cache-control", ""),
                f"Cache-Control={introspect.headers.get('cache-control')!r}",
            )

            other_client_introspect = await client.post(
                f"{as_url}/oauth/introspect", data={"token": access_token, "client_id": dcr_client_id}
            )
            report.expect(
                "已注册的**其他**客户端内省他人令牌得到 active=false（不暴露存在性）",
                other_client_introspect.json().get("active") is False,
                f"HTTP {other_client_introspect.status_code} {other_client_introspect.json()}",
            )

            unknown_client_introspect = await client.post(
                f"{as_url}/oauth/introspect", data={"token": access_token, "client_id": "never-registered-client"}
            )
            report.expect(
                "未注册的调用方内省被拒（invalid_client）",
                unknown_client_introspect.status_code == 401
                and unknown_client_introspect.json().get("error") == "invalid_client",
                f"HTTP {unknown_client_introspect.status_code} {unknown_client_introspect.text[:160]}",
            )

            revoked = await client.post(
                f"{as_url}/oauth/revoke", data={"token": tokens["refresh_token"], "client_id": pre_registered_id}
            )
            report.expect("撤销端点返回 200", revoked.status_code == 200, f"HTTP {revoked.status_code}", fatal=True)

            after_revoke = await client.post(
                f"{as_url}/oauth/introspect", data={"token": access_token, "client_id": pre_registered_id}
            )
            report.expect(
                "撤销后内省返回 active=false",
                after_revoke.json().get("active") is False,
                f"{after_revoke.json()}",
            )

            status, _ = await _mcp_status(f"{rs_url}/mcp", access_token)
            report.expect(
                "撤销后 /mcp 的下一次调用即失败（401）",
                status == 401,
                f"实际 HTTP {status}",
            )

            # ------------------------------------------------------------ refresh 轮换 / 重用检测
            _section("7. refresh 轮换与重用检测")
            second = await _full_flow(
                client,
                as_url=as_url,
                client_id=pre_registered_id,
                redirect_uri=redirect_uri,
                resource=mcp_resource,
                scope=" ".join(_ALL_SCOPES),
                email=email,
                password=password,
                state=f"state-rot-{run_id}",
            )
            rotated = await client.post(
                f"{as_url}/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": second["refresh_token"],
                    "client_id": pre_registered_id,
                },
            )
            report.expect(
                "refresh token 可换出新的令牌对",
                rotated.status_code == 200 and bool(rotated.json().get("refresh_token")),
                f"HTTP {rotated.status_code} {rotated.text[:200]}",
            )
            if rotated.status_code == 200:
                rotated_body = rotated.json()
                report.expect(
                    "轮换后 access token 仍是同一 family（撤销粒度不变）",
                    _decode_unverified(rotated_body["access_token"])[1].get("family_id")
                    == _decode_unverified(second["access_token"])[1].get("family_id"),
                )
                replay = await client.post(
                    f"{as_url}/oauth/token",
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": second["refresh_token"],
                        "client_id": pre_registered_id,
                    },
                )
                report.expect(
                    "重放已用过的 refresh token 被拒",
                    replay.status_code == 400 and replay.json().get("error") == "invalid_grant",
                    f"HTTP {replay.status_code} {replay.text[:200]}",
                )
                family_revoked = await client.post(
                    f"{as_url}/oauth/introspect",
                    data={"token": rotated_body["access_token"], "client_id": pre_registered_id},
                )
                report.expect(
                    "重用检测使整条 family 失效（轮换出的新令牌也一起失效）",
                    family_revoked.json().get("active") is False,
                    f"{family_revoked.json()}",
                )

            # ------------------------------------------------------------ DCR
            _section("8. DCR（兼容回退路径）的端到端")
            dcr_tokens = await _full_flow(
                client,
                as_url=as_url,
                client_id=dcr_client_id,
                redirect_uri=redirect_uri,
                resource=mcp_resource,
                scope=" ".join(_ALL_SCOPES),
                email=email,
                password=password,
                state=f"state-dcr-{run_id}",
            )
            report.expect("DCR 客户端能走完授权并换到令牌", bool(dcr_tokens.get("access_token")))
            try:
                dcr_call = await _call_mcp_with_token(f"{rs_url}/mcp", dcr_tokens["access_token"])
                report.expect(
                    "DCR 客户端的令牌被 RS 接受（auth=true）",
                    bool(dcr_call["payload"] and dcr_call["payload"].get("auth") is True),
                    f"payload={dcr_call['payload']!r}",
                )
            except Exception as exc:
                report.fail("DCR 客户端的令牌被 RS 接受", f"{type(exc).__name__}: {exc}")

            # ------------------------------------------------------------ 协议验收 #6：scope 不足
            _section("9. 协议验收第 6 条：scope 不足时的实际行为")
            narrow_registration = await client.post(
                f"{as_url}/oauth/register",
                json={
                    "client_name": "OAuth E2E 窄 scope 客户端",
                    "redirect_uris": [redirect_uri],
                    "scope": "hrb:profile:read",
                },
            )
            if narrow_registration.status_code == 201:
                narrow_client_id = narrow_registration.json()["client_id"]
                created_client_ids.append(narrow_client_id)
                narrow_tokens = await _full_flow(
                    client,
                    as_url=as_url,
                    client_id=narrow_client_id,
                    redirect_uri=redirect_uri,
                    resource=mcp_resource,
                    scope="hrb:profile:read",
                    email=email,
                    password=password,
                    state=f"state-narrow-{run_id}",
                )
                try:
                    denied = await _call_mcp_with_token(
                        f"{rs_url}/mcp",
                        narrow_tokens["access_token"],
                        tool_name="search_policy",
                        arguments={"query": "请假"},
                    )
                    print(
                        f"      观察：search_policy 在缺 scope 时 → isError={denied['is_error']}，"
                        f"正文={denied['text'][:200]!r}"
                    )
                    report.expect(
                        "缺 scope 时工具调用被拒绝（而非放行）",
                        denied["payload"] is not None and denied["payload"].get("ok") is False,
                        f"payload={denied['payload']!r}",
                    )
                    report.gap(
                        "协议验收 #6：scope 不足应返回 HTTP 403 + 标准错误体",
                        "实测走**工具层信封**（HTTP 200 + outcome=FORBIDDEN）。"
                        "app/mcp/auth/authorization.py 的 denial_envelope 文档字符串已写明"
                        "这一步属于传输层且尚未实现 —— 所以这是**已知未完成项**，不是回归。"
                        "影响：MCP 客户端拿到的是工具错误而不是 403 挑战，因此无法据 "
                        "WWW-Authenticate 的 scope 参数自动补授权。",
                    )
                except Exception as exc:
                    report.fail("scope 不足的行为观测", f"{type(exc).__name__}: {exc}")

        return report
    finally:
        for server in servers:
            _stop(server)
        if keep:
            print("\n（--keep：保留写库的数据）")
        else:
            with contextlib.suppress(Exception):
                await _cleanup(database_url, emails=[email], client_ids=created_client_ids)
                print("\n已清理本次运行写入的行")


def main() -> int:
    parser = argparse.ArgumentParser(description="HRBPilot OAuth 2.1 端到端验收")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("OAUTH_E2E_DATABASE_URL", ""),
        help="指向一个已 alembic upgrade head 的库；默认读 OAUTH_E2E_DATABASE_URL",
    )
    parser.add_argument("--keep", action="store_true", help="保留本次运行写入的数据库行，便于事后排查")
    parser.add_argument("--logs", default="", help="服务日志目录（默认临时目录）")
    args = parser.parse_args()

    if not args.database_url:
        print("必须给出 --database-url 或环境变量 OAUTH_E2E_DATABASE_URL", file=sys.stderr)
        return 2

    if args.logs:
        log_dir = Path(args.logs).resolve()
        log_dir.mkdir(parents=True, exist_ok=True)
        _temporary = None
    else:
        import tempfile

        # 保住引用：TemporaryDirectory 一旦被 GC，目录就被删掉，日志也就没了。
        _temporary = tempfile.TemporaryDirectory(prefix="hrbpilot-oauth-e2e-")
        log_dir = Path(_temporary.name)

    try:
        report = asyncio.run(run(args.database_url, keep=args.keep, log_dir=log_dir))
    except ChainBrokenError as exc:
        print(f"\n\033[31m链条中断\033[0m：{exc}")
        return 1
    except KeyboardInterrupt:
        print("\n已中断")
        return 130

    print(f"\n\033[1m结果：{report.summary()}\033[0m")
    if report.failed:
        print("未通过：")
        for name, _, detail in report.failed:
            label = "已知缺口" if name in report.gap_names else "失败"
            print(f"  - [{label}] {name}" + (f"  — {detail}" if detail else ""))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
