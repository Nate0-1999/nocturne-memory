from __future__ import annotations

import asyncio
from contextlib import nullcontext

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from spine.db.migrate import (
    MIGRATION_ADVISORY_LOCK_ID,
    make_alembic_config,
    migration_lock,
)


class _RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, int]] | str] = []

    def execute(self, statement: object, parameters: dict[str, int]) -> None:
        self.calls.append((str(statement), parameters))

    def commit(self) -> None:
        self.calls.append("commit")


def test_migration_lock_releases_after_success_and_failure() -> None:
    """A-041 serializes C.2 migration work and releases its session lock on every exit."""
    for fail in (False, True):
        connection = _RecordingConnection()
        with pytest.raises(RuntimeError) if fail else nullcontext():
            with migration_lock(connection):  # type: ignore[arg-type]
                if fail:
                    raise RuntimeError("migration failed")

        assert connection.calls == [
            ("SELECT pg_advisory_lock(:lock_id)", {"lock_id": MIGRATION_ADVISORY_LOCK_ID}),
            "commit",
            ("SELECT pg_advisory_unlock(:lock_id)", {"lock_id": MIGRATION_ADVISORY_LOCK_ID}),
            "commit",
        ]


@pytest.mark.parametrize("revision", [f"{number:04d}" for number in range(1, 15)])
def test_every_supported_revision_upgrades_to_head(
    migrated_database_url: str,
    revision: str,
) -> None:
    """SPEC C.2 requires every supported historical schema to reach the one current head."""
    config = make_alembic_config(migrated_database_url)
    try:
        command.downgrade(config, revision)
        command.upgrade(config, "head")
        assert asyncio.run(_database_revision(migrated_database_url)) == "0024"
    finally:
        command.upgrade(config, "head")


async def _database_revision(database_url: str) -> str:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert isinstance(revision, str)
            return revision
    finally:
        await engine.dispose()
