"""Integration proof for the authoritative C.2 migration and C.3 seed."""

import asyncio
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from spine.db.migrate import DATABASE_URL_ATTRIBUTE, make_alembic_config


def test_packaged_migration_tree_has_one_expected_head() -> None:
    """SPEC C.2 is defended by verifying that packaged migration tree has one expected head;
    this prevents drift in the packaged schema migration contract.
    """
    database_url = "postgresql+asyncpg://spine:percent%25@localhost/spine"
    config = make_alembic_config(database_url)
    scripts = ScriptDirectory.from_config(config)

    assert config.attributes[DATABASE_URL_ATTRIBUTE] == database_url
    assert scripts.get_base() == "0001"
    assert scripts.get_heads() == ["0023"]


def test_0004_backfills_legacy_rows_and_downgrades_cleanly(
    migrated_database_url: str,
) -> None:
    """SPEC C.2 requires packaged revisions to upgrade real historical data and downgrade
    cleanly, preventing a migration that works only from an empty schema.
    """

    config = make_alembic_config(migrated_database_url)
    memory_id = uuid4()
    command.downgrade(config, "0003")
    try:
        asyncio.run(_insert_0003_memory(migrated_database_url, memory_id))
        command.upgrade(config, "0004")

        matches = asyncio.run(_legacy_search_matches(migrated_database_url, memory_id))
        assert matches == {
            "label": True,
            "body": True,
            "keyword": True,
        }

        command.downgrade(config, "0003")
        assert asyncio.run(_search_tsv_exists(migrated_database_url)) is False
    finally:
        command.upgrade(config, "head")
        asyncio.run(_delete_memory(migrated_database_url, memory_id))


async def test_c2_migration_and_v0_seed(migrated_database_url: str) -> None:
    """SPEC C.2 is defended by verifying that c2 migration and v0 seed; this prevents drift in
    the packaged schema migration contract.
    """
    engine = create_async_engine(migrated_database_url)
    try:
        async with engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            tables = set(
                (
                    await connection.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = 'public'"
                        )
                    )
                ).scalars()
            )
            extension = await connection.scalar(
                text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
            )
            scorer = (
                (
                    await connection.execute(
                        text(
                            "SELECT version, weights, params, active "
                            "FROM scorer_config WHERE active"
                        )
                    )
                )
                .mappings()
                .one()
            )
            embedding_type = await connection.scalar(
                text(
                    "SELECT format_type(a.atttypid, a.atttypmod) "
                    "FROM pg_attribute a "
                    "JOIN pg_class c ON c.oid = a.attrelid "
                    "WHERE c.relname = 'memory_unit' AND a.attname = 'embedding'"
                )
            )
            search_tsv = (
                (
                    await connection.execute(
                        text(
                            "SELECT format_type(a.atttypid, a.atttypmod) AS data_type, "
                            "a.attnotnull AS not_null "
                            "FROM pg_attribute a "
                            "JOIN pg_class c ON c.oid = a.attrelid "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = 'public' AND c.relname = 'memory_unit' "
                            "AND a.attname = 'search_tsv'"
                        )
                    )
                )
                .mappings()
                .one()
            )
            origin_path = (
                (
                    await connection.execute(
                        text(
                            "SELECT data_type, is_nullable, column_default "
                            "FROM information_schema.columns "
                            "WHERE table_schema = 'public' "
                            "AND table_name = 'memory_unit' AND column_name = 'origin_path'"
                        )
                    )
                )
                .mappings()
                .one()
            )
            active_label_index = await connection.scalar(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' "
                    "AND indexname = 'memory_unit_active_label'"
                )
            )
            search_tsv_index = await connection.scalar(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' "
                    "AND indexname = 'memory_unit_search_tsv_idx'"
                )
            )
            search_tsv_trigger = (
                (
                    await connection.execute(
                        text(
                            "SELECT t.tgenabled::text AS tgenabled, p.proname "
                            "FROM pg_trigger t "
                            "JOIN pg_class c ON c.oid = t.tgrelid "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "JOIN pg_proc p ON p.oid = t.tgfoid "
                            "WHERE n.nspname = 'public' AND c.relname = 'memory_unit' "
                            "AND NOT t.tgisinternal "
                            "AND t.tgname = 'memory_unit_refresh_search_tsv'"
                        )
                    )
                )
                .mappings()
                .one()
            )

            assert revision == "0023"
        expected_tables = {
            "creation_outcome",
            "memory_unit",
            "memory_revision",
            "thread",
            "injection_event",
            "injection_event_annotation",
            "scorer_config",
            "spend_event",
            "spend_reconciliation",
            "memory_edge",
            "approval_queue_item",
            "approval_decision",
            "learner_run",
            "optimization_run",
            "optimization_run_adoption",
            "scorer_activation",
            "transcript_record",
            "curator_trigger_state",
            "curator_run",
            "curator_finding",
            "curator_verdict",
            "curator_action",
        }
        assert expected_tables <= tables
        assert extension == "vector"
        assert embedding_type == "vector(1536)"
        assert search_tsv == {"data_type": "tsvector", "not_null": True}
        assert origin_path == {
            "data_type": "text",
            "is_nullable": "YES",
            "column_default": None,
        }
        assert active_label_index is not None
        assert "UNIQUE INDEX memory_unit_active_label" in active_label_index
        assert "(principal_id, label)" in active_label_index
        assert "WHERE (status = 'active'::text)" in active_label_index
        assert search_tsv_index is not None
        assert "USING gin (search_tsv)" in search_tsv_index
        assert search_tsv_trigger == {
            "tgenabled": "O",
            "proname": "memory_unit_refresh_search_tsv",
        }
        assert scorer["version"] == "m3tl-where-v1"
        assert scorer["active"] is True
        assert scorer["weights"] == {
            "sem": 0.42,
            "kw": 0.16,
            "time": 0.11,
            "proj": 0.16,
            "freq": 0.08,
            "hist": 0.07,
        }
        assert scorer["params"] == {
            "tau": 0.55,
            "top_k": 8,
            "near_miss_k": 3,
            "memory_context_share": 0.10,
            "half_life_time_days": 14,
            "half_life_hist_days": 7,
            "never_bias_step": -0.15,
            "quarantine_kills": 3,
            "candidate_pool": 50,
            "location_weight": 0.08,
            "half_life_location_hops": 2.0,
            "thread_weight": 0.08,
            "where_weight": 0.04,
            "_m3f_activation": {
                "migration": "0014",
                "previous_version": "v0",
                "requirement": "R16",
            },
            "_m3ti_activation": {
                "migration": "0016",
                "amendment": "A-060",
                "previous_version": "m3f-location-v1",
            },
            "_m3ms_activation": {
                "migration": "0017",
                "amendment": "A-061",
                "previous_version": "m3ti-thread-v1",
            },
            "_m3tl_activation": {
                "migration": "0021",
                "amendment": "A-063",
                "previous_version": "m3ms-share-v1",
            },
        }
    finally:
        await engine.dispose()


async def _insert_0003_memory(database_url: str, memory_id: UUID) -> None:
    engine = create_async_engine(database_url)
    embedding = f"[{','.join(['0'] * 1536)}]"
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO memory_unit (
                      id, principal_id, label, body, kind, keywords,
                      embedding, embedding_model
                    ) VALUES (
                      :memory_id, :principal_id, 'LegacyLabelNeedle',
                      'LegacyBodyNeedle', 'fact', ARRAY['LegacyKeywordNeedle'],
                      CAST(:embedding AS vector), 'migration-test'
                    )
                    """
                ),
                {
                    "memory_id": memory_id,
                    "principal_id": f"migration-backfill-{memory_id}",
                    "embedding": embedding,
                },
            )
    finally:
        await engine.dispose()


async def _legacy_search_matches(database_url: str, memory_id: UUID) -> dict[str, bool]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT
                              search_tsv @@ plainto_tsquery(
                                'pg_catalog.simple'::regconfig, 'LegacyLabelNeedle'
                              ) AS label,
                              search_tsv @@ plainto_tsquery(
                                'pg_catalog.simple'::regconfig, 'LegacyBodyNeedle'
                              ) AS body,
                              search_tsv @@ plainto_tsquery(
                                'pg_catalog.simple'::regconfig, 'LegacyKeywordNeedle'
                              ) AS keyword
                            FROM memory_unit
                            WHERE id = :memory_id
                            """
                        ),
                        {"memory_id": memory_id},
                    )
                )
                .mappings()
                .one()
            )
            return dict(row)
    finally:
        await engine.dispose()


async def _search_tsv_exists(database_url: str) -> bool:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return bool(
                await connection.scalar(
                    text(
                        "SELECT EXISTS ("
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'memory_unit' "
                        "AND column_name = 'search_tsv'"
                        ")"
                    )
                )
            )
    finally:
        await engine.dispose()


async def _delete_memory(database_url: str, memory_id: UUID) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM memory_unit WHERE id = :memory_id"),
                {"memory_id": memory_id},
            )
    finally:
        await engine.dispose()


async def test_active_label_is_unique_while_unit_is_active(
    migrated_database_url: str,
) -> None:
    """SPEC C.2 is defended by verifying that active label is unique while unit is active; this
    prevents drift in the packaged schema migration contract.
    """
    engine = create_async_engine(migrated_database_url)
    embedding = f"[{','.join(['0'] * 1536)}]"
    insert = text(
        """
        INSERT INTO memory_unit (
          principal_id, label, body, kind, embedding, embedding_model
        ) VALUES (
          :principal_id, :label, :body, 'fact', CAST(:embedding AS vector), 'test'
        )
        RETURNING id
        """
    )
    values = {
        "principal_id": "owner",
        "label": "stable-handle",
        "body": "first",
        "embedding": embedding,
    }

    try:
        async with engine.begin() as connection:
            first_id = await connection.scalar(insert, values)

            savepoint = await connection.begin_nested()
            with pytest.raises(IntegrityError):
                await connection.execute(insert, values | {"body": "collision"})
            await savepoint.rollback()

            active_count = await connection.scalar(
                text(
                    "SELECT count(*) FROM memory_unit "
                    "WHERE principal_id = :principal_id AND label = :label "
                    "AND status = 'active'"
                ),
                values,
            )

        assert first_id is not None
        assert active_count == 1
    finally:
        await engine.dispose()
