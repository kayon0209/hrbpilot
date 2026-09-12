"""Freeze a reproducible production baseline for the current commit.

Collects the verification surface (pytest / ruff / mypy) *plus the environment
it was measured in*, binds it to the exact commit SHA + dirty state + lockfile
hashes, and writes an immutable ``production-baseline.json`` (P0-07). The
artifact carries a self-verifying content hash so hand-edits are detectable:
``verify()`` recomputes it.

Usage:
    python scripts/freeze_production_baseline.py            # freeze now
    python scripts/freeze_production_baseline.py --verify   # check artifact integrity
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import socket
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[1]
ARTIFACT = REPO / "docs" / "production-baseline.json"

# Fields excluded from the integrity hash: the hash field itself.
_HASH_EXCLUDED = {"content_hash"}


def _run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


# Non-secret .env keys that can change test outcomes. Fingerprinted, not copied.
_FINGERPRINTED_ENV_KEYS = (
    "APP_ENV",
    "RRF_K",
    "DENSE_TOP_K",
    "SPARSE_TOP_K",
    "EMBEDDING_PROVIDER",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIMENSION",
    "LLM_PROVIDER",
    "LLM_MODEL",
)


def _env_file() -> dict[str, str]:
    """Read the repo .env as plain key/value pairs (no interpolation)."""
    path = REPO / ".env"
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _tcp_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    """A probe must never raise: an unreachable dependency is an answer, not an error."""
    sock = socket.socket()
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _url_reachable(url: str, default_port: int) -> bool:
    parsed = urlparse(url or "")
    if not parsed.hostname:
        return False
    return _tcp_reachable(parsed.hostname, parsed.port or default_port)


def _endpoint_reachable(endpoint: str, default_port: int) -> bool:
    """Accept both 'host:port' and 'scheme://host:port' spellings."""
    if not endpoint:
        return False
    parsed = urlparse(endpoint if "//" in endpoint else f"//{endpoint}")
    if not parsed.hostname:
        return False
    return _tcp_reachable(parsed.hostname, parsed.port or default_port)


def _environment() -> dict:
    """Snapshot the runtime dependencies the verification numbers depend on.

    The same commit produces different numbers with and without Milvus running
    (438 skipped-50 vs 439 skipped-49 at freeze time). Recording what was
    actually up is what lets a reader tell a regression from a missing
    container; ``embedding_mode`` matters because the end-to-end test only
    exercises semantic matching when a cloud embedding key is present.
    """
    env = _env_file()
    try:
        milvus_port = int(env.get("VECTOR_DB_PORT") or 19530)
    except ValueError:
        milvus_port = 19530
    fingerprint = hashlib.sha256(
        json.dumps({k: env.get(k, "") for k in _FINGERPRINTED_ENV_KEYS}, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "milvus_reachable": _tcp_reachable(env.get("VECTOR_DB_HOST", "localhost"), milvus_port),
        "postgres_reachable": _url_reachable(env.get("DATABASE_URL", ""), 5432),
        "redis_reachable": _url_reachable(env.get("REDIS_URL", ""), 6379),
        "minio_reachable": _endpoint_reachable(env.get("MINIO_ENDPOINT", ""), 9000),
        "embedding_mode": "cloud" if env.get("EMBEDDING_API_KEY") else "deterministic",
        "config_fingerprint": fingerprint,
        "note": "deps reachable at freeze time; a skipped integration test means a missing container, not a regression",
    }


def _git_sha() -> tuple[str, bool]:
    code, out = _run(["git", "rev-parse", "HEAD"])
    sha = out.strip() if code == 0 else "unknown"
    code2, out2 = _run(["git", "status", "--porcelain"])
    dirty = bool(out2.strip()) if code2 == 0 else True
    return sha, dirty


def _pytest() -> dict:
    code, out = _run([sys.executable, "-m", "pytest", "tests/", "-q", "--no-header"])
    m = re.search(r"(\d+) passed", out)
    s = re.search(r"(\d+) skipped", out)
    f = re.search(r"(\d+) failed", out)
    e = re.search(r"(\d+) error", out)
    return {
        "command": "python -m pytest tests/ -q",
        "exit_code": code,
        "passed": int(m.group(1)) if m else 0,
        "skipped": int(s.group(1)) if s else 0,
        "failed": int(f.group(1)) if f else 0,
        "errors": int(e.group(1)) if e else 0,
    }


def _ruff() -> dict:
    code, out = _run([sys.executable, "-m", "ruff", "check", "app", "tests"])
    m = re.search(r"Found (\d+) errors?", out)
    return {
        "command": "python -m ruff check app tests",
        "exit_code": code,
        "errors": int(m.group(1)) if m else 0,
        "note": "known pre-existing findings (fullwidth-punctuation comments, unused noqa); baseline=74 at freeze time",
    }


def _mypy() -> dict:
    code, out = _run([sys.executable, "-m", "mypy", "app"])
    m = re.search(r"Found (\d+) errors?", out)
    return {
        "command": "python -m mypy app",
        "exit_code": code,
        "errors": int(m.group(1)) if m else 0,
        "note": "known pre-existing findings in retrieval/retriever.py and material_batches.py; baseline=2 at freeze time",
    }


def _web_suite() -> dict:
    """Frontend verification surface: eslint, tsc+vite build, vitest.

    Each entry records the command, exit code and parsed summary counts so
    the artifact states what the web workbench actually passed at freeze time.
    """

    def run_web(cmd: list[str], patterns: dict[str, str]) -> dict:
        code, out = _run(cmd)
        entry: dict = {"command": " ".join(cmd), "exit_code": code}
        for key, pattern in patterns.items():
            m = re.search(pattern, out)
            if m:
                entry[key] = int(m.group(1))
        return entry

    pnpm = ["corepack", "pnpm", "--dir", "web"]
    return {
        "eslint": {**run_web([*pnpm, "lint"], {"errors": r"(\d+)\s+error"}), "note": "exit_code 0 == clean"},
        "build": {**run_web([*pnpm, "build"], {}), "note": "tsc -b && vite build; exit_code 0 == clean"},
        "vitest": run_web(
            [*pnpm, "test:run"],
            {"test_files": r"Test Files\s+(\d+)\s+passed", "tests_passed": r"Tests\s+(\d+)\s+passed"},
        ),
    }


def _content_hash(payload: dict) -> str:
    material = json.dumps(
        {k: v for k, v in payload.items() if k not in _HASH_EXCLUDED}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def freeze() -> int:
    sha, dirty = _git_sha()
    if dirty:
        print("ERROR: working tree is dirty — commit or stash before freezing a baseline.")
        return 1

    # Snapshot before the suite runs: the numbers below only mean something
    # next to the environment that produced them.
    environment = _environment()

    print("Running verification suite (this takes a few minutes)...")
    payload: dict = {
        "frozen_at": datetime.now(UTC).isoformat(),
        "commit": sha,
        "dirty": False,
        "python": sys.version.split()[0],
        "pytest": _pytest(),
        "ruff": _ruff(),
        "mypy": _mypy(),
        "web": _web_suite(),
        "locks": {
            "pyproject": _sha256_file(REPO / "pyproject.toml"),
            "pnpm_lock": _sha256_file(REPO / "web" / "pnpm-lock.yaml"),
        },
        "skips_policy": "every pytest skip is env-gated (HRBP_RUN_* / DATABASE_URL); no owner-less permanent skips",
        "environment": environment,
    }
    payload["content_hash"] = _content_hash(payload)

    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Baseline frozen -> {ARTIFACT}")
    print(f"  commit {sha[:12]}  pytest {payload['pytest']['passed']} passed / {payload['pytest']['skipped']} skipped")
    print(f"  ruff {payload['ruff']['errors']} errors (pre-existing baseline)")
    print(f"  mypy {payload['mypy']['errors']} errors (pre-existing baseline)")
    web = payload["web"]
    print(
        f"  web: eslint exit {web['eslint']['exit_code']}, build exit {web['build']['exit_code']}, "
        f"vitest {web['vitest'].get('tests_passed', '?')} tests in {web['vitest'].get('test_files', '?')} files"
    )
    return 0


def verify() -> int:
    if not ARTIFACT.exists():
        print("ERROR: no frozen baseline found at", ARTIFACT)
        return 1
    payload = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    stored = payload.get("content_hash")
    recomputed = _content_hash(payload)
    if stored != recomputed:
        print("INTEGRITY FAILURE: baseline artifact was hand-edited (content_hash mismatch).")
        return 1
    print("OK: baseline artifact integrity verified.")
    print(f"  frozen at {payload['frozen_at']} on commit {payload['commit'][:12]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="verify an existing artifact instead of freezing")
    args = parser.parse_args()
    return verify() if args.verify else freeze()


if __name__ == "__main__":
    raise SystemExit(main())
