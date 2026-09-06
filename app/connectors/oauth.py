"""OAuth2 authorization-code flow for WeCom / Feishu connectors.

Implements the parts of the certification ladder that make Level 2 possible:

- authorize_url: builds the consent redirect with state (CSRF) protection.
- exchange_code: one-time code → access token, stored only in encrypted form
  (app.connectors.credentials) and never returned to any API response.
- refresh_token: silent renewal before expiry; refresh failures degrade the
  data source to oauth_state=expired, never to silent no-sync.
- get_access_token: cached decryption at use time, auto-refresh inside the
  refresh window.

HTTP calls go through the shared `httpx.AsyncClient` factory so tests can
mock responses deterministically.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

import httpx

from app.connectors.credentials import decrypt_credential, encrypt_credential
from app.connectors.registry import ConnectorSpec
from app.shared.errors import AppError
from app.shared.logger import get_logger

logger = get_logger(__name__)

TIMEOUT_SECONDS = 15.0

# One-time CSRF nonce lifetime. Long enough for an admin to complete the
# provider consent page, short enough to bound replay/abuse.
OAUTH_NONCE_TTL_MINUTES = 15


class OAuthFlowError(AppError):
    def __init__(self, message: str, *, status_code: int = 400, code: str = "OAUTH_ERROR") -> None:
        super().__init__(message, code=code, status_code=status_code)


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT_SECONDS)


def generate_nonce() -> str:
    """Unpredictable, high-entropy one-time CSRF nonce."""
    return secrets.token_urlsafe(32)


def nonce_fingerprint(nonce: str) -> str:
    """SHA-256 fingerprint persisted instead of the plaintext nonce."""
    return hashlib.sha256(nonce.encode("utf-8")).hexdigest()


def nonce_matches(fingerprint: str, candidate: str) -> bool:
    """Constant-time comparison of the stored fingerprint vs. candidate."""
    return hmac.compare_digest(fingerprint, nonce_fingerprint(candidate))


def authorize_url(spec: ConnectorSpec, app_id: str, redirect_uri: str, state: str | None = None) -> str:
    """Build the provider's consent URL. `state` must be validated on return.

    Every query parameter is URL-encoded (no raw f-string concatenation) so a
    redirect_uri or state containing reserved characters cannot corrupt the
    consent URL or smuggle extra parameters to the provider.
    """
    from urllib.parse import urlencode

    state = state or secrets.token_urlsafe(24)
    if spec.platform == "wecom":
        query = urlencode(
            {
                "appid": app_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(spec.oauth_scopes),
                "state": state,
            }
        )
        return f"{spec.oauth_authorize_url}?{query}#wechat_redirect"
    query = urlencode(
        {
            "app_id": app_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"{spec.oauth_authorize_url}?{query}"


async def exchange_code(
    spec: ConnectorSpec,
    app_id: str,
    app_secret: str,
    code: str,
) -> dict:
    """Exchange the one-time authorization code for tokens.

    Returns {"access_token": str, "refresh_token": str|None, "expires_at": datetime,
    "scopes": list[str], "user_id": str|None} — plaintext tokens live only in
    this return value; the caller persists them via encrypt_credential.
    """
    async with _http_client() as client:
        if spec.platform == "wecom":
            # WeCom self-built app: get access_token once, then user identity
            response = await client.get(
                f"{spec.api_base}/gettoken", params={"corpid": app_id, "corpsecret": app_secret}
            )
            payload = response.json()
            if payload.get("errcode") not in (0, None):
                raise OAuthFlowError(f"企业微信授权失败：{payload.get('errmsg')}")
            app_token = payload["access_token"]
            user_response = await client.get(
                f"{spec.api_base}/auth/getuserinfo",
                params={"access_token": app_token, "code": code},
            )
            user_payload = user_response.json()
            if user_payload.get("errcode") not in (0, None):
                raise OAuthFlowError(f"企业微信获取用户信息失败：{user_payload.get('errmsg')}")
            return {
                "access_token": app_token,
                "refresh_token": None,
                "expires_at": datetime.now(UTC) + timedelta(seconds=int(payload.get("expires_in", 7200))),
                "scopes": spec.oauth_scopes,
                "user_id": user_payload.get("userid"),
            }
        # feishu
        response = await client.post(
            spec.oauth_token_url,
            json={
                "grant_type": "authorization_code",
                "client_id": app_id,
                "client_secret": app_secret,
                "code": code,
                "redirect_uri": None,
            },
        )
        payload = response.json()
        if "access_token" not in payload:
            raise OAuthFlowError(f"飞书授权失败：{payload.get('error_description', payload.get('msg'))}")
        return {
            "access_token": payload["access_token"],
            "refresh_token": payload.get("refresh_token"),
            "expires_at": datetime.now(UTC) + timedelta(seconds=int(payload.get("expires_in", 7200))),
            "scopes": payload.get("scope", spec.oauth_scopes),
            "user_id": payload.get("user_id"),
        }


async def refresh_feishu_token(
    app_id: str,
    app_secret: str,
    encrypted_refresh: bytes,
    tenant_id: str,
) -> dict:
    """Renew a Feishu refresh token; returns fresh token material."""
    refresh_token = decrypt_credential(tenant_id, encrypted_refresh)
    async with _http_client() as client:
        response = await client.post(
            "https://open.feishu.cn/open-apis/authen/v2/oauth/token",
            json={
                "grant_type": "refresh_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "refresh_token": refresh_token,
            },
        )
        payload = response.json()
        if "access_token" not in payload:
            raise OAuthFlowError(f"飞书刷新失败：{payload.get('error_description', payload.get('msg'))}")
        return {
            "access_token": payload["access_token"],
            "refresh_token": payload.get("refresh_token", refresh_token),
            "expires_at": datetime.now(UTC) + timedelta(seconds=int(payload.get("expires_in", 7200))),
        }


async def revoke_provider_tokens(
    spec: ConnectorSpec,
    app_id: str,
    app_secret: str,
    refresh_token: str | None,
) -> None:
    """Best-effort provider-side revocation before local token wipe.

    Feishu exposes a revoke endpoint; WeCom self-built apps have no token
    revoke endpoint, so for wecom this is a documented no-op. Callers treat a
    raised exception as a provider outage — local cleanup must still proceed.
    """
    if spec.platform == "wecom":
        return
    if spec.platform == "feishu":
        # 飞书 user access token revoke (authen v1)
        async with _http_client() as client:
            app_token_response = await client.post(
                "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
            )
            token_payload = app_token_response.json()
            if "app_access_token" not in token_payload:
                raise OAuthFlowError(f"飞书撤销失败：{token_payload.get('msg', '无法获取应用令牌')}")
            revoke_body: dict[str, str] = {"app_access_token": token_payload["app_access_token"]}
            if refresh_token:
                revoke_body["refresh_token"] = refresh_token
            response = await client.post(
                "https://open.feishu.cn/open-apis/authen/v1/revoke",
                json=revoke_body,
            )
            payload = response.json()
            if payload.get("code") not in (0, None):
                raise OAuthFlowError(f"飞书撤销失败：{payload.get('msg')}")


def encrypt_token_bundle(tenant_id: str, tokens: dict) -> dict:
    """Persist-ready encrypted bundle. Plaintext never touches storage."""
    bundle: dict[str, bytes | datetime | list[str] | str | None] = {
        "access": encrypt_credential(tenant_id, tokens["access_token"]),
        "expires_at": tokens["expires_at"],
        "scopes": tokens.get("scopes") or [],
        "user_id": tokens.get("user_id"),
    }
    refresh = tokens.get("refresh_token")
    if refresh:
        bundle["refresh"] = encrypt_credential(tenant_id, refresh)
    return bundle


def needs_refresh(expires_at: datetime | None, spec: ConnectorSpec) -> bool:
    if expires_at is None:
        return True
    now = datetime.now(UTC)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return now + timedelta(minutes=spec.token_refresh_minutes_before_expiry) >= expires_at
