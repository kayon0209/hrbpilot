"""Contracts imposed by the existing Alembic version table."""

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_revision_ids_fit_the_existing_version_column() -> None:
    config = Config()
    config.set_main_option("script_location", "app/data/migrations")
    scripts = ScriptDirectory.from_config(config)
    too_long = sorted(revision.revision for revision in scripts.walk_revisions() if len(revision.revision) > 32)
    assert too_long == []
