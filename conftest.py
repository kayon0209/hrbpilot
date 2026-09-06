"""Pytest bootstrap — ensure the project root is importable as ``app``."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
@pytest.fixture(autouse=True)
def _isolate_llm_provider_module_state():
    """Snapshot and restore the LLM orchestrator module globals per test.

    ``_init_providers()`` sets ``_ACTIVE_PROVIDER`` to the configured default
    (e.g. ``zhipu``) on first use and the module never resets it, so any test
    that merely instantiates an orchestrator leaks a non-empty active provider
    into later tests that assert on the untouched-global contract. Restoring
    the snapshot keeps test order independent without changing production
    behavior; the global itself is what v2 §13.1 will remove.
    """

    import app.rag.llm.orchestrator as _orch

    captured = (_orch._PROVIDER_REGISTRY, _orch._ACTIVE_PROVIDER)
    yield
    _orch._PROVIDER_REGISTRY, _orch._ACTIVE_PROVIDER = captured
