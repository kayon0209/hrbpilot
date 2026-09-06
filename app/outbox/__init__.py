"""Transactional outbox claim, dispatch, and completion primitives."""

from app.outbox.dispatcher import (
    DispatchResult,
    OutboxDispatcher,
    RetryableToolDispatchError,
    TerminalToolDispatchError,
    ToolInvocation,
    UnknownToolOutcomeError,
)
from app.outbox.repository import OutboxRepository

__all__ = [
    "DispatchResult",
    "OutboxDispatcher",
    "OutboxRepository",
    "RetryableToolDispatchError",
    "TerminalToolDispatchError",
    "ToolInvocation",
    "UnknownToolOutcomeError",
]
