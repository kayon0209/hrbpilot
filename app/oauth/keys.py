"""授权服务器的签名密钥 —— ES256（ECDSA P-256）。

为什么固定 ES256 而不是 RS256
-----------------------------
两者都是非对称、都能做 JWKS 与轮换。选 ES256 的理由是**令牌体积**：P-256 的签名
是 64 字节、公钥坐标共 64 字节；RSA-2048 的签名是 256 字节、公钥约 270 字节。
MCP 的 access token 出现在**每一个** ``/mcp`` 请求的 ``Authorization`` 头里，还会被
客户端存进本地凭据库 —— 令牌小一半是实打实的收益。ES256 同时也是 WebCrypto、移动端
与各语言 JOSE 库覆盖最完整的曲线（RS256 的兼容优势在这里并不存在）。

``alg`` 由本模块固定，不接受配置：一个可配置的 ``alg`` 字段就是一条降级攻击路径
（把 ``RS256`` 改成 ``none`` 或把非对称改成 HMAC）。签名头里的 ``alg`` 是**我们**
写的，验签方必须按白名单校验 —— 这正是该项目不做成配置的原因。

密钥来源与轮换
--------------
- ``OAUTH_SIGNING_KEY_PEM``：当前**签名**密钥。
- ``OAUTH_ROTATED_PUBLIC_KEYS_PEM``：仅用于**验签**的旧公钥（多块 PEM 拼接）。

轮换流程：生成新密钥 → 设为签名密钥 → 把旧**公钥**放进 rotated → 等所有在途令牌
自然过期 → 清空 rotated。窗口期内新旧令牌都能验，用户不需要重新授权。

``kid`` 用 RFC 7638 的 JWK thumbprint，而不是随机串。thumbprint 完全由密钥本身
决定，因此同一个密钥在任何实例、任何时间算出同一个 ``kid`` —— 多实例部署时不需要
再同步一张 kid 表，也不会出现"A 实例签的令牌，B 实例在 JWKS 里找不到对应公钥"。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import (
    EllipticCurvePrivateKey,
    EllipticCurvePublicKey,
)
from jose import jwt as jose_jwt

from app.config.settings import settings
from app.shared.logger import get_logger

logger = get_logger(__name__)

#: 固定的 JWS 算法。见模块头部：不允许配置。
ALGORITHM = "ES256"
_CURVE = ec.SECP256R1()
#: P-256 的坐标是定长 32 字节。用定长编码而不是 ``int.to_bytes`` 的最小长度 ——
#: 最小长度会让 x 或 y 恰好以 0 字节开头时算出**短于 32 字节**的坐标，而 JWK 要求
#: 定长填充，那样得到的基串与其它实现不一致，thumbprint 也随之不同。
_COORDINATE_BYTES = 32


class SigningKeyError(RuntimeError):
    """密钥配置不可用。

    刻意在**启动期**抛出（由 AS 的 lifespan 主动触发一次），而不是等到第一个客户
    走到"换令牌"那一步：那时错误现场在客户端浏览器里，服务端只留一条普通请求日志。
    """


def _b64url(data: bytes) -> str:
    """JOSE 的无填充 base64url（RFC 7515 §2）。"""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode_pem(value: str) -> bytes:
    """接受 PEM 原文，或 base64 编码后的 PEM。

    环境变量里塞多行 PEM 很容易在编排层被破坏（换行被吞、缩进被加），所以两种都收：
    含 ``-----BEGIN`` 视作原文，否则按 base64 解码。两条路都失败就明确报错，而不是
    把一段乱码丢给 ``load_pem_private_key`` 去猜。
    """
    stripped = value.strip()
    if "-----BEGIN" in stripped:
        return stripped.encode("utf-8")
    try:
        return base64.b64decode(stripped, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SigningKeyError(
            "signing key is neither PEM text nor base64-encoded PEM (expected a -----BEGIN ... PRIVATE KEY----- block)"
        ) from exc


def _load_private_key(pem: bytes) -> EllipticCurvePrivateKey:
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except Exception as exc:  # cryptography 的异常类型很多，统一转成我们的
        raise SigningKeyError("signing key is not a readable PEM private key") from exc
    if not isinstance(key, EllipticCurvePrivateKey):
        raise SigningKeyError(f"signing key must be an EC private key for {ALGORITHM}, got {type(key).__name__}")
    if not isinstance(key.curve, ec.SECP256R1):
        raise SigningKeyError(f"signing key must use curve P-256 (secp256r1) for {ALGORITHM}")
    return key


def _load_public_key(pem: bytes) -> EllipticCurvePublicKey:
    try:
        key = serialization.load_pem_public_key(pem)
    except Exception as exc:
        raise SigningKeyError("rotated verification key is not a readable PEM public key") from exc
    if not isinstance(key, EllipticCurvePublicKey):
        raise SigningKeyError(f"rotated verification key must be an EC public key, got {type(key).__name__}")
    if not isinstance(key.curve, ec.SECP256R1):
        raise SigningKeyError("rotated verification key must use curve P-256 (secp256r1)")
    return key


def _coordinate_pair(public_key: EllipticCurvePublicKey) -> tuple[str, str]:
    numbers = public_key.public_numbers()
    return (
        _b64url(numbers.x.to_bytes(_COORDINATE_BYTES, "big")),
        _b64url(numbers.y.to_bytes(_COORDINATE_BYTES, "big")),
    )


def jwk_thumbprint(x: str, y: str) -> str:
    """RFC 7638 §3.2 的 JWK thumbprint（作为 ``kid``）。

    规范化形式是硬性要求：成员按字典序、无空白、只含必需成员（``crv``/``kty``/``x``/``y``）。
    任何偏差都会让不同实现为**同一个密钥**算出**不同的 kid**，而那正是"令牌验不过
    但看不出为什么"的来源。
    """
    canonical = json.dumps({"crv": "P-256", "kty": "EC", "x": x, "y": y}, separators=(",", ":"), sort_keys=True)
    return _b64url(hashlib.sha256(canonical.encode("ascii")).digest())


def public_jwk(public_key: EllipticCurvePublicKey, kid: str) -> dict[str, str]:
    """EC 公钥 → JWK。只含公开参数 —— 私钥分量 ``d`` 永远不出现在这里。"""
    x, y = _coordinate_pair(public_key)
    return {"kty": "EC", "crv": "P-256", "x": x, "y": y, "kid": kid, "use": "sig", "alg": ALGORITHM}


@dataclass(frozen=True)
class SigningKey:
    """当前用于签发的密钥。``private_pem`` 是 PKCS#8 未加密 PEM。"""

    kid: str
    private_pem: bytes
    public_key: EllipticCurvePublicKey

    def public_jwk(self) -> dict[str, str]:
        return public_jwk(self.public_key, self.kid)

    def sign(self, claims: dict[str, object], *, token_type: str = "at+jwt") -> str:
        """签出紧凑序列化的 JWS（RFC 7515 §7.1）。

        ``kid`` 放进头部，验签方据此在 JWKS 里选公钥 —— 轮换窗口期会有多个公钥并存，
        没有 ``kid`` 就只能逐个试。

        ``typ`` 默认是 RFC 9068 §2.1 给 **JWT 形式的访问令牌**规定的 ``at+jwt``：
        它让接收方能一眼区分"这是访问令牌"与"这是别的什么 JWT"。本 AS 只签发访问
        令牌，所以默认值就是正确值；参数留着是为了将来若加入 ID Token 之类的类型时
        不必回来改签名路径。
        """
        return str(
            jose_jwt.encode(
                claims,
                self.private_pem,
                algorithm=ALGORITHM,
                headers={"kid": self.kid, "typ": token_type},
            )
        )


@dataclass(frozen=True)
class VerificationKey:
    """仅用于验签的公钥（含当前签名密钥的公钥）。"""

    kid: str
    public_key: EllipticCurvePublicKey

    def jwk(self) -> dict[str, str]:
        return public_jwk(self.public_key, self.kid)


def _build(private_key: EllipticCurvePrivateKey) -> SigningKey:
    public_key = private_key.public_key()
    x, y = _coordinate_pair(public_key)
    return SigningKey(
        kid=jwk_thumbprint(x, y),
        private_pem=private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        public_key=public_key,
    )


def signing_key_from_pem(pem_text: str) -> SigningKey:
    """从 PEM 构造签名密钥。测试与配置加载共用这一条路径。"""
    return _build(_load_private_key(_decode_pem(pem_text)))


def generate_signing_key() -> SigningKey:
    """生成一把新的 P-256 密钥。用于本地开发与轮换时的密钥生成。"""
    return _build(ec.generate_private_key(_CURVE))


def _split_pem_blocks(value: str) -> list[bytes]:
    """把拼接的多块 PEM 拆成单块。

    只认块边界（``BEGIN``/``END``），忽略块外的空白与注释 —— 运维把两块 PEM 拼进
    一个变量时，中间常常夹着空行或一句 ``# 旧密钥``。
    """
    blocks: list[bytes] = []
    current: list[str] = []
    for line in value.splitlines():
        if "-----BEGIN" in line:
            current = [line]
        elif current:
            current.append(line)
            if "-----END" in line:
                blocks.append("\n".join(current).encode("utf-8"))
                current = []
    return blocks


_active: SigningKey | None = None
_verification: tuple[VerificationKey, ...] | None = None


def _load_active_signing_key() -> SigningKey:
    configured = settings.oauth_signing_key_pem
    if configured.strip():
        return signing_key_from_pem(configured)
    if settings.is_production:
        # settings 的 validator 已经挡过这一条。这里是纵深防御：本模块是公开的，
        # 未来可能有脚本绕过 settings 直接调用它。
        raise SigningKeyError("OAUTH_SIGNING_KEY_PEM must be configured in staging or production")
    logger.warning(
        "oauth_signing_key_ephemeral",
        msg="OAUTH_SIGNING_KEY_PEM is unset — generated an in-process key; issued tokens die with this process",
    )
    return generate_signing_key()


def active_signing_key() -> SigningKey:
    """当前签名密钥。首次调用时加载并缓存。

    缓存是有意的：从 PEM 解析密钥每次都要走一遍 ASN.1，而签令牌在每次授权与每次
    刷新时都会发生。轮换后调用 ``reset_signing_key_cache()`` 让它重新加载。
    """
    global _active
    if _active is None:
        _active = _load_active_signing_key()
    return _active


def verification_keys() -> tuple[VerificationKey, ...]:
    """可用于验签的全部公钥：当前签名密钥 + ``OAUTH_ROTATED_PUBLIC_KEYS_PEM``。"""
    global _verification
    if _verification is None:
        active = active_signing_key()
        keys: list[VerificationKey] = [VerificationKey(kid=active.kid, public_key=active.public_key)]
        for block in _split_pem_blocks(settings.oauth_rotated_public_keys_pem):
            rotated = _load_public_key(block)
            x, y = _coordinate_pair(rotated)
            keys.append(VerificationKey(kid=jwk_thumbprint(x, y), public_key=rotated))
        _verification = tuple(keys)
    return _verification


def public_jwks() -> list[dict[str, str]]:
    """RFC 7517 §5 的 JWK Set 文档（只有公钥）。"""
    return [key.jwk() for key in verification_keys()]


def reset_signing_key_cache() -> None:
    """清空密钥缓存。测试与轮换后重新加载共用。"""
    global _active, _verification
    _active = None
    _verification = None
