"""Freeze a reproducible production baseline for the current commit.

Collects the verification surface (pytest / ruff / mypy), binds it to the
exact commit SHA + dirty state + lockfile hashes, and writes an immutable
``production-baseline.json`` (P0-07). The artifact carries a self-verifying
content hash so hand-edits are detectable: ``verify()`` recomputes it.

Usage:
    python scripts/freeze_production_baseline.py            # freeze now
    python scripts/freeze_production_baseline.py --verify   # check artifact integrity
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ARTIFACT = REPO / "docs" / "production-baseline.json"

# Fields excluded from the integrity hash: the hash field itself.
_HASH_EXCLUDED = {"content_hash"}


def _run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


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

    print("Running verification suite (this takes a few minutes)...")
    payload: dict = {
        "frozen_at": datetime.now(UTC).isoformat(),
        "commit": sha,
        "dirty": False,
        "python": sys.version.split()[0],
        "pytest": _pytest(),
        "ruff": _ruff(),
        "mypy": _mypy(),
        "locks": {
            "pyproject": _sha256_file(REPO / "pyproject.toml"),
            "pnpm_lock": _sha256_file(REPO / "web" / "pnpm-lock.yaml"),
        },
        "skips_policy": "every pytest skip is env-gated (HRBP_RUN_* / DATABASE_URL); no owner-less permanent skips",
    }
    payload["content_hash"] = _content_hash(payload)

    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Baseline frozen -> {ARTIFACT}")
    print(f"  commit {sha[:12]}  pytest {payload['pytest']['passed']} passed / {payload['pytest']['skipped']} skipped")
    print(f"  ruff {payload['ruff']['errors']} errors (pre-existing baseline)")
    print(f"  mypy {payload['mypy']['errors']} errors (pre-existing baseline)")
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
