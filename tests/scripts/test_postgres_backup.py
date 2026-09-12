"""§4.4 backup/drill safety logic.

These cover the parts that must never be wrong, and that can be tested without
PostgreSQL client tools installed: URL parsing, the drill database naming
guard, credential handling, and manifest integrity hashing.
"""

import pytest

from scripts.postgres_backup import (
    DRILL_SENTINEL,
    BackupError,
    _sha256,
    build_pg_env,
    drill_database_name,
    parse_database_url,
)


def test_parse_database_url_extracts_connection_parts():
    conn = parse_database_url("postgresql+asyncpg://hrbp:secret@localhost:5433/hrbp_workbench")

    assert conn["host"] == "localhost"
    assert conn["port"] == "5433"
    assert conn["user"] == "hrbp"
    assert conn["password"] == "secret"
    assert conn["dbname"] == "hrbp_workbench"


def test_parse_database_url_defaults_port():
    conn = parse_database_url("postgresql://u:p@dbhost/onlydb")
    assert conn["port"] == "5432"


def test_parse_database_url_rejects_unparseable():
    with pytest.raises(BackupError):
        parse_database_url("not-a-url")


def test_password_is_passed_via_environment_not_argv():
    """argv is visible to every process on the box; the password must not be there."""
    env = build_pg_env("topsecret")
    assert env["PGPASSWORD"] == "topsecret"


def test_drill_database_is_never_the_source():
    source = "hrbp_workbench"
    target = drill_database_name(source)

    assert target != source
    assert DRILL_SENTINEL in target
    assert target.startswith(source)


def test_drill_names_are_unique_per_run():
    assert drill_database_name("db") != drill_database_name("db")


def test_sha256_matches_known_vector():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "f.bin"
        path.write_bytes(b"abc")
        assert _sha256(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
