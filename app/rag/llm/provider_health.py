"""Observed LLM provider health — what actually happened, not a guess.

Why this module exists
----------------------
``/api/ready`` used to report ``status: ok`` while DeepSeek was returning 402
(insufficient balance) on every single policy-QA request and the router quietly
served everything from the fallback provider. Nothing in the readiness payload
reflected it, so an operator watching ``/api/ready`` saw a perfectly healthy
instance.

The fix is deliberately **not** a synthetic probe:

* there is no cheap "is the vendor up" call — a real probe spends tokens on
  every probe interval, which is exactly what a tight Kubernetes
  ``readinessProbe`` would do;
* a probe would also report on a *different* code path than the one that
  actually serves traffic (different model, different request shape), so it can
  be green while real requests fail.

Instead this module records the outcome of calls that really happened, in
process, with no network I/O of its own. ``/api/ready`` reports that
observation. A provider that failed and has not succeeded since is reported as
degraded for ``WINDOW_SECONDS`` — long enough to be visible to an operator,
short enough that a single transient blip does not pin the instance to
``degraded`` forever.

Scope note: this is per-process. A multi-replica deployment sees only its own
observations, which is the right granularity for a readiness probe.
"""

from __future__ import annotations

import time
from threading import Lock

# 5 minutes: visible on a dashboard, self-healing after a transient blip.
WINDOW_SECONDS = 300.0

_lock = Lock()
# provider -> monotonic timestamp of the last failure (cleared on success)
_failed_at: dict[str, float] = {}


def record_success(provider: str) -> None:
    """A call to ``provider`` completed — clear any prior degradation."""
    with _lock:
        _failed_at.pop(provider, None)


def record_failure(provider: str) -> None:
    """A call to ``provider`` raised — mark it degraded until it succeeds."""
    with _lock:
        _failed_at[provider] = time.monotonic()


def degraded_providers() -> list[str]:
    """Providers that failed within the window and have not recovered.

    Sorted so the value is stable for logs and tests.
    """
    now = time.monotonic()
    with _lock:
        return sorted(p for p, failed_at in _failed_at.items() if now - failed_at <= WINDOW_SECONDS)


def reset() -> None:
    """Forget every observation (tests only)."""
    with _lock:
        _failed_at.clear()
