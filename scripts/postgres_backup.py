"""Personal-grade PostgreSQL backup + restore drill (§4.4).

Two commands:

    python scripts/postgres_backup.py backup
    python scripts/postgres_backup.py restore-drill [--dump PATH]

Why a drill mode exists: an untested backup is a belief, not a backup. The
drill restores into a THROWAWAY database and then drops it, so you learn
whether the dump actually restores without ever endangering real data.

Safety rules enforced by this script (not by convention):
  * the password is passed via PGPASSWORD, never on the command line
    (argv is visible to every process on the machine);
  * the password is never written to the manifest or the logs;
  * restore-drill refuses to target the source database — the drill database
    name is always suffixed and asserted different from the source;
  * backups never overwrite an existing file.

Requires pg_dump / pg_restore / createdb / dropdb on PATH (PostgreSQL client
tools). The script reports a clear message when they are missing instead of
failing halfway.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[1]
BACKUP_DIR = REPO / "backups"
MANIFEST_SUFFIX = ".manifest.json"
DRILL_SENTINEL = "_drill_"

DEFAULT_URL = "postgresql+asyncpg://hrbp:hrbp_password@localhost:5432/hrbp_workbench"


class BackupError(RuntimeError):
    """Raised for operator-facing failures (missing tools, unsafe target)."""


def _load_env_url() -> str:
    """Read DATABASE_URL from the environment, falling back to .env."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env_file = REPO / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return DEFAULT_URL


def parse_database_url(url: str) -> dict[str, str]:
    """Split a SQLAlchemy-style URL into plain connection parts.

    ``postgresql+asyncpg://user:pass@host:port/db`` → the driver suffix is
    dropped so the result is usable by the psql-family tools.
    """
    cleaned = url.replace("+asyncpg", "").replace("+psycopg", "")
    parsed = urlparse(cleaned)
    if not parsed.hostname or not parsed.path.lstrip("/"):
        raise BackupError(f"DATABASE_URL 无法解析出主机与库名: {url}")
    return {
        "host": parsed.hostname,
        "port": str(parsed.port or 5432),
        "user": parsed.username or "postgres",
        "password": parsed.password or "",
        "dbname": parsed.path.lstrip("/"),
    }


def build_pg_env(password: str) -> dict[str, str]:
    """Environment for the psql family, with the password kept out of argv."""
    env = dict(os.environ)
    if password:
        env["PGPASSWORD"] = password
    return env


def drill_database_name(source: str) -> str:
    """Name for the throwaway restore target — never the source database.

    Carries a random suffix as well as a timestamp: a second-resolution stamp
    alone collides when two drills start in the same second, and the second
    run would then try to restore into a database that already exists.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"{source}{DRILL_SENTINEL}{stamp}_{uuid.uuid4().hex[:8]}"


def _require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise BackupError(
            f"未找到 {name}。请安装 PostgreSQL 客户端工具并将其加入 PATH（备份/演练依赖官方 pg_dump 而非 Python 实现）。"
        )


def _run(cmd: list[str], env: dict[str, str]) -> None:
    """Run a command, surfacing a readable error instead of a traceback."""
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        # Never echo the environment (it carries PGPASSWORD).
        raise BackupError(f"命令失败 [{' '.join(cmd)}]: {result.stderr.strip() or result.stdout.strip()}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def backup(url: str, out_dir: Path = BACKUP_DIR) -> Path:
    """Dump the database to a timestamped file and record a manifest."""
    _require_tool("pg_dump")
    conn = parse_database_url(url)
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dump_path = out_dir / f"pg-{conn['dbname']}-{stamp}.dump"
    if dump_path.exists():
        raise BackupError(f"备份文件已存在，拒绝覆盖: {dump_path}")

    cmd = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--host",
        conn["host"],
        "--port",
        conn["port"],
        "--username",
        conn["user"],
        "--dbname",
        conn["dbname"],
        "--file",
        str(dump_path),
    ]
    _run(cmd, build_pg_env(conn["password"]))

    manifest = {
        "dump": dump_path.name,
        "created_at": datetime.now(UTC).isoformat(),
        "database": conn["dbname"],
        "host": conn["host"],
        "port": conn["port"],
        "user": conn["user"],
        "size_bytes": dump_path.stat().st_size,
        "sha256": _sha256(dump_path),
        "tool": "pg_dump --format=custom",
    }
    manifest_path = dump_path.with_suffix(dump_path.suffix + MANIFEST_SUFFIX)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[backup] {dump_path}")
    print(f"[backup] manifest: {manifest_path.name}  sha256={manifest['sha256'][:16]}…")
    return dump_path


def _verify_dump_integrity(dump_path: Path) -> None:
    """Compare the file against its recorded sha256 (detects silent corruption)."""
    manifest_path = dump_path.with_suffix(dump_path.suffix + MANIFEST_SUFFIX)
    if not manifest_path.exists():
        print("[drill] 未找到 manifest，跳过完整性校验")
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual = _sha256(dump_path)
    if actual != manifest.get("sha256"):
        raise BackupError(f"备份文件完整性校验失败: 期望 {manifest.get('sha256')} 实际 {actual}")
    print("[drill] 备份完整性校验通过")


def restore_drill(url: str, dump_path: Path | None = None) -> None:
    """Restore into a throwaway database, verify it, then drop it."""
    for tool in ("pg_restore", "createdb", "dropdb", "psql"):
        _require_tool(tool)

    conn = parse_database_url(url)
    if dump_path is None:
        candidates = sorted(BACKUP_DIR.glob("pg-*.dump"))
        if not candidates:
            raise BackupError(f"未找到备份文件于 {BACKUP_DIR}，请先执行 backup")
        dump_path = candidates[-1]

    _verify_dump_integrity(dump_path)

    target = drill_database_name(conn["dbname"])
    if target == conn["dbname"] or DRILL_SENTINEL not in target:
        raise BackupError("演练目标库必须是一次性库，绝不能是源库")

    env = build_pg_env(conn["password"])
    base = ["--host", conn["host"], "--port", conn["port"], "--username", conn["user"]]
    print(f"[drill] 源库={conn['dbname']} 目标一次性库={target}")
    try:
        _run(["createdb", *base, target], env)
        _run(
            ["pg_restore", *base, "--dbname", target, "--no-owner", "--no-privileges", str(dump_path)],
            env,
        )
        # Verify the restore actually produced tables, not just a silent no-op.
        result = subprocess.run(
            [
                "psql",
                *base,
                "--dbname",
                target,
                "--tuples-only",
                "--no-align",
                "--command",
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';",
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise BackupError(f"演练校验查询失败: {result.stderr.strip()}")
        table_count = int((result.stdout or "0").strip().splitlines()[0] or 0)
        if table_count <= 0:
            raise BackupError("演练恢复后未发现任何表 —— 该备份不可用")
        print(f"[drill] 恢复成功，public schema 表数量={table_count}")
    finally:
        # Always clean up: a leftover drill database is clutter at best and a
        # confusing target for the next run at worst.
        subprocess.run(["dropdb", *base, "--if-exists", target], env=env, capture_output=True, check=False)
        print(f"[drill] 已清理一次性库 {target}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("backup", help="dump the database into ./backups with a manifest")
    drill = sub.add_parser("restore-drill", help="restore into a throwaway database and drop it")
    drill.add_argument("--dump", type=Path, default=None, help="specific dump file (default: latest)")

    args = parser.parse_args(argv)
    url = _load_env_url()
    try:
        if args.command == "backup":
            backup(url)
        else:
            restore_drill(url, args.dump)
    except BackupError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
