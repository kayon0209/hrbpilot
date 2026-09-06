"""Protocol-shaped, network-isolated simulator for WeCom application messages.

The module intentionally contains no HTTP client.  It models only the two
server-side protocol steps that the delivery service needs to reason about:
``gettoken`` followed by a text ``message/send`` result.  Every generated
token and message id is synthetic and local-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

FaultMode = Literal["none", "timeout", "invalid_token_once"]


@dataclass(frozen=True)
class SimulatedToken:
    """A synthetic token used exclusively inside the local simulator."""

    value: str
    expires_in: int = 7200


@dataclass(frozen=True)
class SimulatedWeComResponse:
    """The safe subset of an application-message response used by the outbox."""

    errcode: int
    errmsg: str
    msgid: str | None = None
    invaliduser: str | None = None
    retryable: bool = False


class WeComOutboundSimulator:
    """A deterministic WeCom-shaped gateway that never performs network I/O.

    ``fault_mode`` and ``invalid_users`` exist only to make retry and terminal
    rejection behaviour testable.  They cannot be configured from an API.
    """

    def __init__(
        self,
        *,
        fault_mode: FaultMode = "none",
        invalid_users: set[str] | None = None,
    ) -> None:
        if fault_mode not in {"none", "timeout", "invalid_token_once"}:
            raise ValueError("unsupported simulator fault_mode")
        self._fault_mode = fault_mode
        self._invalid_users = invalid_users or set()
        self._issued_tokens: set[str] = set()
        self._token_sequence = 0
        self._message_sequence = 0
        self._invalid_token_consumed = False

    async def get_token(self, corp_id: str, corp_secret: str) -> SimulatedToken:
        """Return a synthetic token without retaining or transmitting credentials."""
        if not corp_id.strip():
            raise ValueError("corp_id is required for the local protocol simulation")
        if not corp_secret.strip():
            raise ValueError("corp_secret is required for the local protocol simulation")
        self._token_sequence += 1
        digest = sha256(f"{corp_id}:{self._token_sequence}:local-simulator".encode()).hexdigest()[:20]
        token = SimulatedToken(value=f"sim-wecom-token-{digest}")
        self._issued_tokens.add(token.value)
        return token

    async def send_text(
        self,
        access_token: str,
        agent_id: str,
        touser: str,
        content: str,
    ) -> SimulatedWeComResponse:
        """Return a deterministic application-message result without sending it."""
        if not access_token or access_token not in self._issued_tokens:
            return SimulatedWeComResponse(errcode=42001, errmsg="access_token expired", retryable=True)
        if not agent_id.isdigit():
            raise ValueError("agent_id must be numeric")
        if not touser.strip() or "/" in touser:
            raise ValueError("touser must be a non-empty internal member id")
        if not content.strip():
            raise ValueError("content is required")
        if touser in self._invalid_users:
            return SimulatedWeComResponse(
                errcode=60111,
                errmsg="invalid user",
                invaliduser=touser,
                retryable=False,
            )
        if self._fault_mode == "invalid_token_once" and not self._invalid_token_consumed:
            self._invalid_token_consumed = True
            return SimulatedWeComResponse(errcode=42001, errmsg="access_token expired", retryable=True)
        if self._fault_mode == "timeout":
            return SimulatedWeComResponse(errcode=-1, errmsg="system busy", retryable=True)

        self._message_sequence += 1
        digest = sha256(f"{agent_id}:{touser}:{content}:{self._message_sequence}:local-simulator".encode()).hexdigest()[
            :20
        ]
        return SimulatedWeComResponse(
            errcode=0,
            errmsg="ok",
            msgid=f"sim-wecom-{digest}",
            retryable=False,
        )
