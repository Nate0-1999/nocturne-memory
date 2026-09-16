"""S1 proofs for literal C.2 mappings, CAS history, and tombstones."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import CheckConstraint, func, insert, select, text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.schema import CreateTable

from spine.db.memory import (
    CasUpdate,
    MemoryCasConflictError,
    MemoryLineageError,
    MemoryUnitChanges,
    MemoryUnitSnapshot,
    cas_update_memory_unit,
    tombstone_memory_unit,
)
from spine.db.models import Base, MemoryRevision, MemoryUnit
from spine.db.session import make_session_factory

ZERO_EMBEDDING = (0.0,) * 1536
SEEDED_AT = datetime(2020, 1, 1, tzinfo=UTC)


@pytest.fixture
async def database(
    migrated_database_url: str,
) -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]]]:
    engine = create_async_engine(migrated_database_url)
    try:
        yield engine, make_session_factory(engine)
    finally:
        await engine.dispose()


def _rev_uid(value: int) -> str:
    """Return a deterministic 26-character Crockford-compatible test ULID."""

    return f"{value:026d}"


async def _insert_memory_and_root(
    session: AsyncSession,
    *,
    memory_id: UUID,
    root_uid: str,
    principal_id: str,
    label: str,
    body: str,
    keywords: tuple[str, ...] = (),
) -> None:
    await session.execute(
        insert(MemoryUnit).values(
            id=memory_id,
            principal_id=principal_id,
            label=label,
            body=body,
            kind="fact",
            keywords=list(keywords),
            embedding=list(ZERO_EMBEDDING),
            embedding_model="s1-test",
            created_at=SEEDED_AT,
            updated_at=SEEDED_AT,
        )
    )
    await session.execute(
        insert(MemoryRevision).values(
            rev_uid=root_uid,
            parent_uid=None,
            memory_id=memory_id,
            revision=1,
            body=body,
            label=label,
            editor="user",
            origin_machine_id="machine-root",
            reason="create",
        )
    )


async def _seed_memory(
    sessions: async_sessionmaker[AsyncSession],
    *,
    memory_id: UUID,
    root_uid: str,
    principal_id: str,
    label: str,
    body: str = "root body",
    keywords: tuple[str, ...] = (),
) -> None:
    async with sessions.begin() as session:
        await _insert_memory_and_root(
            session,
            memory_id=memory_id,
            root_uid=root_uid,
            principal_id=principal_id,
            label=label,
            body=body,
            keywords=keywords,
        )


async def _search_tsv_matches(session: AsyncSession, memory_id: UUID, term: str) -> bool:
    return bool(
        await session.scalar(
            text(
                "SELECT search_tsv @@ "
                "plainto_tsquery('pg_catalog.simple'::regconfig, :term) "
                "FROM memory_unit WHERE id = :memory_id"
            ),
            {"memory_id": memory_id, "term": term},
        )
    )


async def test_models_match_authoritative_c2_schema(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """SPEC C.2 is defended by verifying that models match authoritative c2 schema; this
    prevents drift in the authoritative revisioned storage contract.
    """
    engine, _ = database

    def schema_diff(connection: Any) -> list[Any]:
        context = MigrationContext.configure(
            connection,
            opts={"compare_type": True, "compare_server_default": True},
        )
        return compare_metadata(context, Base.metadata)

    async with engine.connect() as connection:
        differences = await connection.run_sync(schema_diff)

    assert differences == []
    assert tuple(Base.metadata.tables) == (
        "memory_unit",
        "memory_revision",
        "memory_edge",
        "approval_queue_item",
        "approval_decision",
        "curator_trigger_state",
        "curator_run",
        "curator_finding",
        "curator_verdict",
        "curator_action",
        "symphony_run_resolution",
        "thread",
        "injection_event",
        "injection_event_annotation",
        "spend_event",
        "spend_reconciliation",
        "scorer_config",
        "learner_run",
        "optimization_run",
        "scorer_activation",
        "optimization_run_adoption",
        "transcript_record",
        "creation_outcome",
        "curator_progress",
    )

    expected_columns = {
        "curator_progress": (
            "event_id", "principal_id", "run_uid", "phase", "memory_ids",
            "finding_uid", "action", "ts",
        ),
        "creation_outcome": (
            "event_key",
            "memory_id",
            "principal_id",
            "machine_id",
            "source",
            "outcome",
            "reason",
            "ts",
        ),
        "memory_unit": (
            "id",
            "principal_id",
            "label",
            "body",
            "kind",
            "keywords",
            "search_tsv",
            "embedding",
            "embedding_model",
            "project_key",
            "thread_origin",
            "origin_thread_id",
            "origin_path",
            "origin_location",
            "run_id",
            "origin_agent",
            "pin",
            "status",
            "revision",
            "stats",
            "bias",
            "created_at",
            "updated_at",
        ),
        "memory_revision": (
            "rev_uid",
            "parent_uid",
            "memory_id",
            "revision",
            "body",
            "label",
            "editor",
            "origin_machine_id",
            "reason",
            "ts",
        ),
        "memory_edge": (
            "edge_uid",
            "from_memory_id",
            "to_memory_id",
            "edge_type",
            "created_at",
        ),
        "approval_queue_item": (
            "item_uid",
            "candidate_memory_id",
            "principal_id",
            "birthplace",
            "birthplace_thread_id",
            "batch_uid",
            "source_name",
            "source_sha256",
            "birthplace_run_id",
            "birthplace_origin_agent",
            "judged_context",
            "candidate_revision",
            "curator_run_uid",
            "curator_finding_uid",
            "proposal_payload",
            "verdict",
            "neighbor_ids",
            "target_ids",
            "state",
            "created_at",
            "decided_at",
        ),
        "approval_decision": (
            "decision_uid",
            "item_uid",
            "decision",
            "approval_mode",
            "actor_class",
            "created_at",
        ),
        "curator_trigger_state": (
            "principal_id",
            "admitted_writes",
            "last_run_writes",
            "pressure_events",
            "last_run_pressure",
            "updated_at",
        ),
        "curator_run": (
            "run_uid",
            "principal_id",
            "trigger",
            "report_version",
            "report",
            "admitted_writes_snapshot",
            "pressure_snapshot",
            "verdict_count",
            "queued_count",
            "executed_count",
            "status",
            "error",
            "started_at",
            "completed_at",
        ),
        "curator_finding": (
            "finding_uid",
            "run_uid",
            "ordinal",
            "kind",
            "memory_ids",
            "evidence",
            "fingerprint",
            "created_at",
        ),
        "curator_verdict": (
            "verdict_uid",
            "finding_uid",
            "action",
            "rationale",
            "proposal",
            "created_at",
        ),
        "curator_action": (
            "action_uid",
            "verdict_uid",
            "finding_uid",
            "queue_item_uid",
            "outcome",
            "detail",
            "created_at",
        ),
        "symphony_run_resolution": (
            "run_id",
            "principal_id",
            "batch_uid",
            "winner_origin_agent",
            "machine_id",
            "judged_context",
            "created_at",
        ),
        "thread": (
            "id",
            "principal_id",
            "agent_id",
            "machine_id",
            "project_key",
            "snapshot_ts",
            "created_at",
        ),
        "injection_event": (
            "id",
            "event_uid",
            "injection_id",
            "thread_id",
            "agent_id",
            "machine_id",
            "principal_id",
            "project_key",
            "agent_kind",
            "prompt_text",
            "scorer_version",
            "memory_id",
            "memory_kind",
            "features",
            "score",
            "rank",
            "shown_as",
            "actor_class",
            "outcome",
            "ts",
        ),
        "injection_event_annotation": (
            "target_event_uid",
            "kind",
            "target_principal_id",
            "target_machine_id",
            "reason",
            "annotator_principal_id",
            "annotator_machine_id",
            "annotator_origin_agent",
            "ts",
        ),
        "spend_event": (
            "event_uid",
            "ts",
            "product_type",
            "quantity_type",
            "unit_of_measure",
            "quantity",
            "cost_usd",
            "basis",
            "behavior",
            "purpose",
            "principal_id",
            "machine_id",
            "origin_agent",
            "thread_id",
            "run_id",
            "prompt_id",
            "memory_id",
            "model",
            "provider",
            "quantization",
            "ref",
            "meta",
        ),
        "spend_reconciliation": (
            "event_uid",
            "ts",
            "provider",
            "status",
            "broker_usage_usd",
            "ledger_cost_usd",
            "broker_since_baseline_usd",
            "ledger_since_baseline_usd",
            "drift_usd",
            "tolerance_usd",
            "unpriced_lines",
            "error_code",
        ),
        "scorer_config": ("version", "weights", "params", "created_at", "active"),
        "learner_run": (
            "run_uid",
            "trigger",
            "result",
            "incumbent_version",
            "proposal_version",
            "eligible_dispositions",
            "training_dispositions",
            "holdout_dispositions",
            "training_pairs",
            "source_boundary",
            "incumbent",
            "challenger",
            "reason",
            "ts",
        ),
        "optimization_run": (
            "run_uid",
            "loop",
            "trigger_kind",
            "trigger_event_uid",
            "trigger_thread_id",
            "corpus_fingerprint",
            "corpus_size",
            "corpus_max_size",
            "corpus_stratification",
            "incumbent_version",
            "challenger_version",
            "incumbent_params",
            "challenger_params",
            "backtest_scores",
            "verdict",
            "tie_break_applied",
            "cost_refs",
            "started_at",
            "completed_at",
        ),
        "scorer_activation": (
            "event_uid",
            "version",
            "previous_version",
            "actor_class",
            "machine_id",
            "reason",
            "changes",
            "ts",
        ),
        "optimization_run_adoption": ("run_uid", "tap_event_uid", "adopted_at"),
        "transcript_record": (
            "principal_id",
            "thread_id",
            "sequence",
            "journal_line",
            "sha256",
            "received_at",
        ),
    }
    assert {
        name: tuple(table.c.keys()) for name, table in Base.metadata.tables.items()
    } == expected_columns

    nullable = {
        name: {column.name for column in table.c if column.nullable}
        for name, table in Base.metadata.tables.items()
    }
    assert nullable == {
        "curator_progress": {"finding_uid", "action"},
        "creation_outcome": set(),
        "memory_unit": {
            "project_key",
            "thread_origin",
            "origin_thread_id",
            "origin_path",
            "origin_location",
            "run_id",
            "origin_agent",
        },
        "memory_revision": {"parent_uid", "revision"},
        "memory_edge": set(),
        "approval_queue_item": {
            "birthplace_thread_id",
            "batch_uid",
            "source_name",
            "source_sha256",
            "birthplace_run_id",
            "birthplace_origin_agent",
            "judged_context",
            "candidate_revision",
            "curator_run_uid",
            "curator_finding_uid",
            "proposal_payload",
            "decided_at",
        },
        "approval_decision": set(),
        "curator_trigger_state": set(),
        "curator_run": {"error"},
        "curator_finding": set(),
        "curator_verdict": set(),
        "curator_action": {"queue_item_uid"},
        "symphony_run_resolution": set(),
        "thread": {"project_key", "snapshot_ts"},
        "injection_event": {"project_key", "outcome"},
        "injection_event_annotation": set(),
        "spend_event": {
            "cost_usd",
            "principal_id",
            "machine_id",
            "origin_agent",
            "thread_id",
            "run_id",
            "prompt_id",
            "memory_id",
            "model",
            "provider",
            "quantization",
        },
        "spend_reconciliation": {
            "broker_usage_usd",
            "ledger_cost_usd",
            "broker_since_baseline_usd",
            "ledger_since_baseline_usd",
            "drift_usd",
            "error_code",
        },
        "scorer_config": set(),
        "learner_run": {"proposal_version", "source_boundary", "incumbent", "challenger"},
        "optimization_run": {"trigger_thread_id", "challenger_version", "challenger_params"},
        "scorer_activation": set(),
        "optimization_run_adoption": set(),
        "transcript_record": set(),
    }

    primary_keys = {
        name: tuple(column.name for column in table.primary_key.columns)
        for name, table in Base.metadata.tables.items()
    }
    assert primary_keys == {
        "curator_progress": ("event_id",),
        "creation_outcome": ("event_key",),
        "memory_unit": ("id",),
        "memory_revision": ("rev_uid",),
        "memory_edge": ("edge_uid",),
        "approval_queue_item": ("item_uid",),
        "approval_decision": ("decision_uid",),
        "curator_trigger_state": ("principal_id",),
        "curator_run": ("run_uid",),
        "curator_finding": ("finding_uid",),
        "curator_verdict": ("verdict_uid",),
        "curator_action": ("action_uid",),
        "symphony_run_resolution": ("run_id",),
        "thread": ("id",),
        "injection_event": ("id",),
        "injection_event_annotation": ("target_event_uid",),
        "spend_event": ("event_uid",),
        "spend_reconciliation": ("event_uid",),
        "scorer_config": ("version",),
        "learner_run": ("run_uid",),
        "optimization_run": ("run_uid",),
        "scorer_activation": ("event_uid",),
        "optimization_run_adoption": ("run_uid",),
        "transcript_record": ("principal_id", "thread_id", "sequence"),
    }

    dialect = postgresql.dialect()
    types = {
        f"{name}.{column.name}": column.type.compile(dialect=dialect)
        for name, table in Base.metadata.tables.items()
        for column in table.c
    }
    assert types == {
        "curator_progress.event_id": "BIGINT",
        "curator_progress.principal_id": "TEXT",
        "curator_progress.run_uid": "TEXT",
        "curator_progress.phase": "TEXT",
        "curator_progress.memory_ids": "UUID[]",
        "curator_progress.finding_uid": "TEXT",
        "curator_progress.action": "TEXT",
        "curator_progress.ts": "TIMESTAMP WITH TIME ZONE",
        "creation_outcome.event_key": "TEXT",
        "creation_outcome.memory_id": "UUID",
        "creation_outcome.principal_id": "TEXT",
        "creation_outcome.machine_id": "TEXT",
        "creation_outcome.source": "TEXT",
        "creation_outcome.outcome": "TEXT",
        "creation_outcome.reason": "TEXT",
        "creation_outcome.ts": "TIMESTAMP WITH TIME ZONE",
        "memory_unit.id": "UUID",
        "memory_unit.principal_id": "TEXT",
        "memory_unit.label": "TEXT",
        "memory_unit.body": "TEXT",
        "memory_unit.kind": "TEXT",
        "memory_unit.keywords": "TEXT[]",
        "memory_unit.search_tsv": "TSVECTOR",
        "memory_unit.embedding": "VECTOR(1536)",
        "memory_unit.embedding_model": "TEXT",
        "memory_unit.project_key": "TEXT",
        "memory_unit.thread_origin": "TEXT",
        "memory_unit.origin_thread_id": "UUID",
        "memory_unit.origin_path": "TEXT",
        "memory_unit.origin_location": "TEXT",
        "memory_unit.run_id": "TEXT",
        "memory_unit.origin_agent": "TEXT",
        "memory_unit.pin": "BOOLEAN",
        "memory_unit.status": "TEXT",
        "memory_unit.revision": "INTEGER",
        "memory_unit.stats": "JSONB",
        "memory_unit.bias": "REAL",
        "memory_unit.created_at": "TIMESTAMP WITH TIME ZONE",
        "memory_unit.updated_at": "TIMESTAMP WITH TIME ZONE",
        "memory_revision.rev_uid": "TEXT",
        "memory_revision.parent_uid": "TEXT",
        "memory_revision.memory_id": "UUID",
        "memory_revision.revision": "INTEGER",
        "memory_revision.body": "TEXT",
        "memory_revision.label": "TEXT",
        "memory_revision.editor": "TEXT",
        "memory_revision.origin_machine_id": "TEXT",
        "memory_revision.reason": "TEXT",
        "memory_revision.ts": "TIMESTAMP WITH TIME ZONE",
        "memory_edge.edge_uid": "TEXT",
        "memory_edge.from_memory_id": "UUID",
        "memory_edge.to_memory_id": "UUID",
        "memory_edge.edge_type": "TEXT",
        "memory_edge.created_at": "TIMESTAMP WITH TIME ZONE",
        "approval_queue_item.item_uid": "TEXT",
        "approval_queue_item.candidate_memory_id": "UUID",
        "approval_queue_item.principal_id": "TEXT",
        "approval_queue_item.birthplace": "TEXT",
        "approval_queue_item.birthplace_thread_id": "UUID",
        "approval_queue_item.batch_uid": "UUID",
        "approval_queue_item.source_name": "TEXT",
        "approval_queue_item.source_sha256": "TEXT",
        "approval_queue_item.birthplace_run_id": "TEXT",
        "approval_queue_item.birthplace_origin_agent": "TEXT",
        "approval_queue_item.judged_context": "JSONB",
        "approval_queue_item.candidate_revision": "INTEGER",
        "approval_queue_item.curator_run_uid": "TEXT",
        "approval_queue_item.curator_finding_uid": "TEXT",
        "approval_queue_item.proposal_payload": "JSONB",
        "approval_queue_item.verdict": "TEXT",
        "approval_queue_item.neighbor_ids": "JSONB",
        "approval_queue_item.target_ids": "JSONB",
        "approval_queue_item.state": "TEXT",
        "approval_queue_item.created_at": "TIMESTAMP WITH TIME ZONE",
        "approval_queue_item.decided_at": "TIMESTAMP WITH TIME ZONE",
        "approval_decision.decision_uid": "TEXT",
        "approval_decision.item_uid": "TEXT",
        "approval_decision.decision": "TEXT",
        "approval_decision.approval_mode": "TEXT",
        "approval_decision.actor_class": "TEXT",
        "approval_decision.created_at": "TIMESTAMP WITH TIME ZONE",
        "curator_trigger_state.principal_id": "TEXT",
        "curator_trigger_state.admitted_writes": "BIGINT",
        "curator_trigger_state.last_run_writes": "BIGINT",
        "curator_trigger_state.pressure_events": "BIGINT",
        "curator_trigger_state.last_run_pressure": "BIGINT",
        "curator_trigger_state.updated_at": "TIMESTAMP WITH TIME ZONE",
        "curator_run.run_uid": "TEXT",
        "curator_run.principal_id": "TEXT",
        "curator_run.trigger": "TEXT",
        "curator_run.report_version": "TEXT",
        "curator_run.report": "JSONB",
        "curator_run.admitted_writes_snapshot": "BIGINT",
        "curator_run.pressure_snapshot": "BIGINT",
        "curator_run.verdict_count": "INTEGER",
        "curator_run.queued_count": "INTEGER",
        "curator_run.executed_count": "INTEGER",
        "curator_run.status": "TEXT",
        "curator_run.error": "TEXT",
        "curator_run.started_at": "TIMESTAMP WITH TIME ZONE",
        "curator_run.completed_at": "TIMESTAMP WITH TIME ZONE",
        "curator_finding.finding_uid": "TEXT",
        "curator_finding.run_uid": "TEXT",
        "curator_finding.ordinal": "INTEGER",
        "curator_finding.kind": "TEXT",
        "curator_finding.memory_ids": "JSONB",
        "curator_finding.evidence": "JSONB",
        "curator_finding.fingerprint": "TEXT",
        "curator_finding.created_at": "TIMESTAMP WITH TIME ZONE",
        "curator_verdict.verdict_uid": "TEXT",
        "curator_verdict.finding_uid": "TEXT",
        "curator_verdict.action": "TEXT",
        "curator_verdict.rationale": "TEXT",
        "curator_verdict.proposal": "JSONB",
        "curator_verdict.created_at": "TIMESTAMP WITH TIME ZONE",
        "curator_action.action_uid": "TEXT",
        "curator_action.verdict_uid": "TEXT",
        "curator_action.finding_uid": "TEXT",
        "curator_action.queue_item_uid": "TEXT",
        "curator_action.outcome": "TEXT",
        "curator_action.detail": "JSONB",
        "curator_action.created_at": "TIMESTAMP WITH TIME ZONE",
        "symphony_run_resolution.run_id": "TEXT",
        "symphony_run_resolution.principal_id": "TEXT",
        "symphony_run_resolution.batch_uid": "UUID",
        "symphony_run_resolution.winner_origin_agent": "TEXT",
        "symphony_run_resolution.machine_id": "TEXT",
        "symphony_run_resolution.judged_context": "JSONB",
        "symphony_run_resolution.created_at": "TIMESTAMP WITH TIME ZONE",
        "thread.id": "UUID",
        "thread.principal_id": "TEXT",
        "thread.agent_id": "TEXT",
        "thread.machine_id": "TEXT",
        "thread.project_key": "TEXT",
        "thread.snapshot_ts": "TIMESTAMP WITH TIME ZONE",
        "thread.created_at": "TIMESTAMP WITH TIME ZONE",
        "injection_event.id": "BIGINT",
        "injection_event.event_uid": "TEXT",
        "injection_event.injection_id": "UUID",
        "injection_event.thread_id": "UUID",
        "injection_event.agent_id": "TEXT",
        "injection_event.machine_id": "TEXT",
        "injection_event.principal_id": "TEXT",
        "injection_event.project_key": "TEXT",
        "injection_event.agent_kind": "TEXT",
        "injection_event.prompt_text": "TEXT",
        "injection_event.scorer_version": "TEXT",
        "injection_event.memory_id": "UUID",
        "injection_event.memory_kind": "TEXT",
        "injection_event.features": "JSONB",
        "injection_event.score": "REAL",
        "injection_event.rank": "INTEGER",
        "injection_event.shown_as": "TEXT",
        "injection_event.actor_class": "TEXT",
        "injection_event.outcome": "TEXT",
        "injection_event.ts": "TIMESTAMP WITH TIME ZONE",
        "injection_event_annotation.target_event_uid": "TEXT",
        "injection_event_annotation.kind": "TEXT",
        "injection_event_annotation.target_principal_id": "TEXT",
        "injection_event_annotation.target_machine_id": "TEXT",
        "injection_event_annotation.reason": "TEXT",
        "injection_event_annotation.annotator_principal_id": "TEXT",
        "injection_event_annotation.annotator_machine_id": "TEXT",
        "injection_event_annotation.annotator_origin_agent": "TEXT",
        "injection_event_annotation.ts": "TIMESTAMP WITH TIME ZONE",
        "spend_event.event_uid": "TEXT",
        "spend_event.ts": "TIMESTAMP WITH TIME ZONE",
        "spend_event.product_type": "TEXT",
        "spend_event.quantity_type": "TEXT",
        "spend_event.unit_of_measure": "TEXT",
        "spend_event.quantity": "NUMERIC(30, 9)",
        "spend_event.cost_usd": "NUMERIC(20, 12)",
        "spend_event.basis": "TEXT",
        "spend_event.behavior": "TEXT",
        "spend_event.purpose": "TEXT",
        "spend_event.principal_id": "TEXT",
        "spend_event.machine_id": "TEXT",
        "spend_event.origin_agent": "TEXT",
        "spend_event.thread_id": "UUID",
        "spend_event.run_id": "TEXT",
        "spend_event.prompt_id": "TEXT",
        "spend_event.memory_id": "UUID",
        "spend_event.model": "TEXT",
        "spend_event.provider": "TEXT",
        "spend_event.quantization": "TEXT",
        "spend_event.ref": "TEXT",
        "spend_event.meta": "JSONB",
        "spend_reconciliation.event_uid": "TEXT",
        "spend_reconciliation.ts": "TIMESTAMP WITH TIME ZONE",
        "spend_reconciliation.provider": "TEXT",
        "spend_reconciliation.status": "TEXT",
        "spend_reconciliation.broker_usage_usd": "NUMERIC(20, 12)",
        "spend_reconciliation.ledger_cost_usd": "NUMERIC(20, 12)",
        "spend_reconciliation.broker_since_baseline_usd": "NUMERIC(20, 12)",
        "spend_reconciliation.ledger_since_baseline_usd": "NUMERIC(20, 12)",
        "spend_reconciliation.drift_usd": "NUMERIC(20, 12)",
        "spend_reconciliation.tolerance_usd": "NUMERIC(20, 12)",
        "spend_reconciliation.unpriced_lines": "BIGINT",
        "spend_reconciliation.error_code": "TEXT",
        "scorer_config.version": "TEXT",
        "scorer_config.weights": "JSONB",
        "scorer_config.params": "JSONB",
        "scorer_config.created_at": "TIMESTAMP WITH TIME ZONE",
        "scorer_config.active": "BOOLEAN",
        "learner_run.run_uid": "TEXT",
        "learner_run.trigger": "TEXT",
        "learner_run.result": "TEXT",
        "learner_run.incumbent_version": "TEXT",
        "learner_run.proposal_version": "TEXT",
        "learner_run.eligible_dispositions": "BIGINT",
        "learner_run.training_dispositions": "BIGINT",
        "learner_run.holdout_dispositions": "BIGINT",
        "learner_run.training_pairs": "BIGINT",
        "learner_run.source_boundary": "TEXT",
        "learner_run.incumbent": "JSONB",
        "learner_run.challenger": "JSONB",
        "learner_run.reason": "TEXT",
        "learner_run.ts": "TIMESTAMP WITH TIME ZONE",
        "optimization_run.run_uid": "TEXT",
        "optimization_run.loop": "TEXT",
        "optimization_run.trigger_kind": "TEXT",
        "optimization_run.trigger_event_uid": "TEXT",
        "optimization_run.trigger_thread_id": "UUID",
        "optimization_run.corpus_fingerprint": "TEXT",
        "optimization_run.corpus_size": "BIGINT",
        "optimization_run.corpus_max_size": "BIGINT",
        "optimization_run.corpus_stratification": "JSONB",
        "optimization_run.incumbent_version": "TEXT",
        "optimization_run.challenger_version": "TEXT",
        "optimization_run.incumbent_params": "JSONB",
        "optimization_run.challenger_params": "JSONB",
        "optimization_run.backtest_scores": "JSONB",
        "optimization_run.verdict": "TEXT",
        "optimization_run.tie_break_applied": "JSONB",
        "optimization_run.cost_refs": "JSONB",
        "optimization_run.started_at": "TIMESTAMP WITH TIME ZONE",
        "optimization_run.completed_at": "TIMESTAMP WITH TIME ZONE",
        "scorer_activation.event_uid": "TEXT",
        "scorer_activation.version": "TEXT",
        "scorer_activation.previous_version": "TEXT",
        "scorer_activation.actor_class": "TEXT",
        "scorer_activation.machine_id": "TEXT",
        "scorer_activation.reason": "TEXT",
        "scorer_activation.changes": "JSONB",
        "scorer_activation.ts": "TIMESTAMP WITH TIME ZONE",
        "optimization_run_adoption.run_uid": "TEXT",
        "optimization_run_adoption.tap_event_uid": "TEXT",
        "optimization_run_adoption.adopted_at": "TIMESTAMP WITH TIME ZONE",
        "transcript_record.principal_id": "TEXT",
        "transcript_record.thread_id": "UUID",
        "transcript_record.sequence": "BIGINT",
        "transcript_record.journal_line": "TEXT",
        "transcript_record.sha256": "TEXT",
        "transcript_record.received_at": "TIMESTAMP WITH TIME ZONE",
    }

    defaults = {
        f"{name}.{column.name}": str(column.server_default.arg.compile(dialect=dialect))
        for name, table in Base.metadata.tables.items()
        for column in table.c
        if column.server_default is not None
    }
    assert defaults == {
        "curator_progress.memory_ids": "'{}'::uuid[]",
        "curator_progress.ts": "clock_timestamp()",
        "memory_unit.id": "gen_random_uuid()",
        "memory_unit.keywords": "'{}'",
        "memory_unit.pin": "false",
        "memory_unit.status": "'active'",
        "memory_unit.revision": "1",
        "memory_unit.stats": (
            '\'{"injections":0,"removals":0,"citations":0,'
            '"never_kills":0,"last_injected_at":null}\'::jsonb'
        ),
        "memory_unit.bias": "0.0",
        "memory_unit.created_at": "now()",
        "memory_unit.updated_at": "now()",
        "memory_revision.reason": "''",
        "memory_revision.ts": "now()",
        "memory_edge.created_at": "now()",
        "approval_queue_item.birthplace": "'thread'",
        "approval_queue_item.state": "'pending'",
        "approval_queue_item.created_at": "now()",
        "approval_decision.created_at": "now()",
        "curator_trigger_state.admitted_writes": "0",
        "curator_trigger_state.last_run_writes": "0",
        "curator_trigger_state.pressure_events": "0",
        "curator_trigger_state.last_run_pressure": "0",
        "curator_trigger_state.updated_at": "now()",
        "curator_run.started_at": "now()",
        "curator_run.completed_at": "now()",
        "curator_finding.created_at": "now()",
        "curator_verdict.created_at": "now()",
        "curator_action.created_at": "now()",
        "symphony_run_resolution.created_at": "now()",
        "thread.created_at": "now()",
        "injection_event.agent_kind": "'general'",
        "injection_event.actor_class": "'human'",
        "injection_event.ts": "now()",
        "injection_event_annotation.ts": "now()",
        "spend_event.meta": "'{}'::jsonb",
        "spend_reconciliation.ts": "now()",
        "scorer_config.created_at": "now()",
        "scorer_config.active": "false",
        "learner_run.ts": "now()",
        "optimization_run.cost_refs": "'[]'::jsonb",
        "scorer_activation.ts": "now()",
        "optimization_run_adoption.adopted_at": "now()",
        "transcript_record.received_at": "now()",
    }
    memory_unit_ddl = str(CreateTable(Base.metadata.tables["memory_unit"]).compile(dialect=dialect))
    assert (
        "stats JSONB DEFAULT "
        '\'{"injections":0,"removals":0,"citations":0,'
        '"never_kills":0,"last_injected_at":null}\'::jsonb NOT NULL' in memory_unit_ddl
    )

    checks = {
        name: {
            constraint.name: str(constraint.sqltext)
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        }
        for name, table in Base.metadata.tables.items()
    }
    assert checks == {
        "curator_progress": {
            "curator_progress_phase_check": (
                "phase IN ('run.started','finding.started','finding.completed',"
                "'run.completed','run.failed')"
            ),
        },
        "creation_outcome": {},
        "memory_unit": {
            "memory_unit_kind_check": (
                "kind IN ('fact','preference','procedure','project_note','persona','pinned')"
            ),
            "memory_unit_status_check": (
                "status IN ('active','candidate','staged','quarantined','tombstoned')"
            ),
            "memory_unit_run_lineage_pair_check": ("(run_id IS NULL) = (origin_agent IS NULL)"),
            "memory_unit_staged_lineage_check": "status <> 'staged' OR run_id IS NOT NULL",
        },
        "memory_revision": {},
        "memory_edge": {
            "memory_edge_type_check": (
                "edge_type IN ('merged_from','supersedes','contradicts','relates_to')"
            )
        },
        "approval_queue_item": {
            "approval_queue_item_birthplace_check": (
                "birthplace IN ('thread','seed','symphony','curator')"
            ),
            "approval_queue_item_birthplace_shape_check": (
                "(birthplace = 'thread' AND birthplace_thread_id IS NOT NULL "
                "AND batch_uid IS NULL AND source_name IS NULL AND source_sha256 IS NULL "
                "AND birthplace_run_id IS NULL AND birthplace_origin_agent IS NULL "
                "AND judged_context IS NULL AND curator_run_uid IS NULL "
                "AND curator_finding_uid IS NULL AND proposal_payload IS NULL "
                "AND candidate_revision IS NULL) OR "
                "(birthplace = 'seed' AND birthplace_thread_id IS NULL "
                "AND batch_uid IS NOT NULL AND source_name IS NOT NULL "
                "AND source_sha256 IS NOT NULL "
                "AND birthplace_run_id IS NULL AND birthplace_origin_agent IS NULL "
                "AND judged_context IS NULL AND curator_run_uid IS NULL "
                "AND curator_finding_uid IS NULL AND proposal_payload IS NULL "
                "AND candidate_revision IS NULL) OR "
                "(birthplace = 'symphony' AND birthplace_thread_id IS NULL "
                "AND batch_uid IS NOT NULL AND source_name IS NULL AND source_sha256 IS NULL "
                "AND birthplace_run_id IS NOT NULL AND birthplace_origin_agent IS NOT NULL "
                "AND judged_context IS NOT NULL AND curator_run_uid IS NULL "
                "AND curator_finding_uid IS NULL AND proposal_payload IS NULL "
                "AND candidate_revision IS NULL) OR "
                "(birthplace = 'curator' AND birthplace_thread_id IS NULL "
                "AND batch_uid IS NULL AND source_name IS NULL AND source_sha256 IS NULL "
                "AND birthplace_run_id IS NULL AND birthplace_origin_agent IS NULL "
                "AND judged_context IS NULL AND curator_run_uid IS NOT NULL "
                "AND curator_finding_uid IS NOT NULL AND proposal_payload IS NOT NULL "
                "AND candidate_revision IS NOT NULL)"
            ),
            "approval_queue_item_state_check": ("state IN ('pending','approved','rejected')"),
            "approval_queue_item_verdict_check": (
                "verdict IN "
                "('new','merge','supersede','contradict','retire','keyword_repair','split')"
            ),
        },
        "approval_decision": {
            "approval_decision_actor_check": "actor_class IN ('human','passive')",
            "approval_decision_mode_check": ("approval_mode IN ('explicit','passive')"),
            "approval_decision_value_check": "decision IN ('approve','deny')",
        },
        "curator_trigger_state": {
            "curator_trigger_admitted_check": "admitted_writes >= 0",
            "curator_trigger_cursor_check": (
                "last_run_writes >= 0 AND last_run_writes <= admitted_writes"
            ),
            "curator_trigger_pressure_check": "pressure_events >= 0",
            "curator_trigger_pressure_cursor_check": (
                "last_run_pressure >= 0 AND last_run_pressure <= pressure_events"
            ),
        },
        "curator_run": {
            "curator_run_trigger_check": (
                "trigger IN ('writes','manual','injection_pressure','cron')"
            ),
            "curator_run_status_check": "status IN ('completed','failed')",
            "curator_run_writes_check": "admitted_writes_snapshot >= 0",
            "curator_run_pressure_check": "pressure_snapshot >= 0",
            "curator_run_counts_check": (
                "verdict_count >= 0 AND queued_count >= 0 AND executed_count >= 0"
            ),
            "curator_run_error_shape_check": (
                "(status = 'completed' AND error IS NULL) OR "
                "(status = 'failed' AND error IS NOT NULL)"
            ),
        },
        "curator_finding": {
            "curator_finding_ordinal_check": "ordinal >= 0",
            "curator_finding_kind_check": (
                "kind IN ('duplicate','contradiction','stale','slop','keyword')"
            ),
            "curator_finding_fingerprint_check": "fingerprint ~ '^[0-9a-f]{64}$'",
        },
        "curator_verdict": {
            "curator_verdict_action_check": (
                "action IN "
                "('keep','merge','contradict','supersede','retire','keyword_repair','split')"
            ),
        },
        "curator_action": {
            "curator_action_outcome_check": ("outcome IN ('queued','executed','noop','refused')"),
        },
        "symphony_run_resolution": {},
        "thread": {},
        "injection_event": {
            "injection_event_actor_class_check": ("actor_class IN ('human','passive')"),
            "injection_event_shown_as_check": (
                "shown_as IN ('injected','near_miss','pinned','budget_cut')"
            ),
        },
        "injection_event_annotation": {
            "injection_event_annotation_annotator_agent_check": (
                "annotator_origin_agent = btrim(annotator_origin_agent) "
                "AND annotator_origin_agent <> ''"
            ),
            "injection_event_annotation_annotator_machine_check": (
                "annotator_machine_id = btrim(annotator_machine_id) AND annotator_machine_id <> ''"
            ),
            "injection_event_annotation_annotator_principal_check": (
                "annotator_principal_id = btrim(annotator_principal_id) "
                "AND annotator_principal_id <> ''"
            ),
            "injection_event_annotation_kind_check": "kind = 'verification_only'",
            "injection_event_annotation_reason_check": ("reason = btrim(reason) AND reason <> ''"),
        },
        "spend_event": {
            "spend_event_product_type_check": (
                "product_type IN ('llm.request','llm.embedding','llm.fee',"
                "'infra.db.instance','infra.db.storage','infra.run.serve',"
                "'infra.run.job','infra.observability','net.egress','fleet.lease',"
                "'fleet.snapshot') OR product_type LIKE 'ext.api._%'"
            ),
            "spend_event_quantity_type_nonblank_check": (
                "quantity_type = btrim(quantity_type) AND quantity_type <> ''"
            ),
            "spend_event_unit_of_measure_nonblank_check": (
                "unit_of_measure = btrim(unit_of_measure) AND unit_of_measure <> ''"
            ),
            "spend_event_quantity_check": "quantity > 0",
            "spend_event_cost_usd_check": "cost_usd IS NULL OR cost_usd >= 0",
            "spend_event_basis_check": "basis IN ('measured','allocated','estimated')",
            "spend_event_behavior_check": "behavior IN ('variable','fixed','step')",
            "spend_event_purpose_check": (
                "purpose IN "
                "('building','extraction','curation','judge','remember','embedding','scout')"
            ),
            "spend_event_ref_nonblank_check": "ref = btrim(ref) AND ref <> ''",
        },
        "spend_reconciliation": {
            "spend_reconciliation_provider_check": "provider = 'openrouter'",
            "spend_reconciliation_status_check": (
                "status IN ('baseline','balanced','drift','unavailable')"
            ),
            "spend_reconciliation_tolerance_check": "tolerance_usd >= 0",
            "spend_reconciliation_unpriced_check": "unpriced_lines >= 0",
            "spend_reconciliation_error_check": (
                "error_code IS NULL OR error_code IN "
                "('broker_unavailable','invalid_broker_response')"
            ),
            "spend_reconciliation_shape_check": (
                "(status = 'unavailable' AND broker_usage_usd IS NULL "
                "AND ledger_cost_usd IS NULL AND broker_since_baseline_usd IS NULL "
                "AND ledger_since_baseline_usd IS NULL AND drift_usd IS NULL "
                "AND error_code IS NOT NULL) OR "
                "(status = 'baseline' AND broker_usage_usd IS NOT NULL "
                "AND ledger_cost_usd IS NOT NULL AND broker_since_baseline_usd = 0 "
                "AND ledger_since_baseline_usd = 0 AND drift_usd = 0 "
                "AND error_code IS NULL) OR "
                "(status IN ('balanced','drift') AND broker_usage_usd IS NOT NULL "
                "AND ledger_cost_usd IS NOT NULL AND broker_since_baseline_usd IS NOT NULL "
                "AND ledger_since_baseline_usd IS NOT NULL AND drift_usd IS NOT NULL "
                "AND error_code IS NULL)"
            ),
        },
        "scorer_config": {},
        "learner_run": {
            "learner_run_trigger_check": "trigger IN ('manual','background')",
            "learner_run_result_check": ("result IN ('insufficient_data','not_better','proposed')"),
            "learner_run_eligible_dispositions_check": "eligible_dispositions >= 0",
            "learner_run_training_dispositions_check": "training_dispositions >= 0",
            "learner_run_holdout_dispositions_check": "holdout_dispositions >= 0",
            "learner_run_training_pairs_check": "training_pairs >= 0",
        },
        "optimization_run": {
            "optimization_run_loop_check": "loop = btrim(loop) AND loop <> ''",
            "optimization_run_trigger_kind_check": (
                "trigger_kind = btrim(trigger_kind) AND trigger_kind <> ''"
            ),
            "optimization_run_trigger_event_check": (
                "trigger_event_uid = btrim(trigger_event_uid) AND trigger_event_uid <> ''"
            ),
            "optimization_run_corpus_fingerprint_check": ("corpus_fingerprint ~ '^[0-9a-f]{64}$'"),
            "optimization_run_corpus_size_check": "corpus_size >= 0",
            "optimization_run_corpus_max_size_check": "corpus_max_size > 0",
            "optimization_run_stratification_check": (
                "jsonb_typeof(corpus_stratification) = 'object'"
            ),
            "optimization_run_incumbent_params_check": (
                "jsonb_typeof(incumbent_params) = 'object'"
            ),
            "optimization_run_challenger_params_check": (
                "challenger_params IS NULL OR jsonb_typeof(challenger_params) = 'object'"
            ),
            "optimization_run_backtest_scores_check": ("jsonb_typeof(backtest_scores) = 'object'"),
            "optimization_run_verdict_check": "verdict = btrim(verdict) AND verdict <> ''",
            "optimization_run_tie_break_check": ("jsonb_typeof(tie_break_applied) = 'object'"),
            "optimization_run_cost_refs_check": "jsonb_typeof(cost_refs) = 'array'",
            "optimization_run_corpus_bound_check": "corpus_size <= corpus_max_size",
            "optimization_run_time_span_check": "completed_at >= started_at",
        },
        "scorer_activation": {
            "scorer_activation_actor_class_check": ("actor_class IN ('human','passive')"),
            "scorer_activation_reason_check": (
                "reason IN ('human_control','learner_proposal','contract_migration')"
            ),
        },
        "optimization_run_adoption": {},
        "transcript_record": {
            "transcript_record_principal_check": (
                "principal_id = btrim(principal_id) AND principal_id <> ''"
            ),
            "transcript_record_sequence_check": "sequence > 0",
            "transcript_record_sha256_check": "sha256 ~ '^[0-9a-f]{64}$'",
        },
    }

    unit = Base.metadata.tables["memory_unit"]
    indexes = {index.name: index for index in unit.indexes}
    assert set(indexes) == {
        "memory_unit_embedding_idx",
        "memory_unit_search_tsv_idx",
        "memory_unit_principal_id_status_project_key_idx",
        "memory_unit_principal_run_origin_status_idx",
        "memory_unit_principal_origin_thread_idx",
        "memory_unit_active_label",
    }
    assert indexes["memory_unit_embedding_idx"].dialect_options["postgresql"]["using"] == "hnsw"
    assert indexes["memory_unit_embedding_idx"].dialect_options["postgresql"]["ops"] == {
        "embedding": "vector_cosine_ops"
    }
    assert indexes["memory_unit_search_tsv_idx"].dialect_options["postgresql"]["using"] == "gin"
    assert (
        str(
            indexes["memory_unit_principal_origin_thread_idx"].dialect_options["postgresql"][
                "where"
            ]
        )
        == "origin_thread_id IS NOT NULL"
    )
    assert indexes["memory_unit_active_label"].unique is True
    assert (
        str(indexes["memory_unit_active_label"].dialect_options["postgresql"]["where"])
        == "status = 'active'"
    )

    revision = Base.metadata.tables["memory_revision"]
    assert {foreign_key.target_fullname for foreign_key in revision.c.parent_uid.foreign_keys} == {
        "memory_revision.rev_uid"
    }
    assert {foreign_key.target_fullname for foreign_key in revision.c.memory_id.foreign_keys} == {
        "memory_unit.id"
    }

    edge = Base.metadata.tables["memory_edge"]
    assert {foreign_key.target_fullname for foreign_key in edge.c.from_memory_id.foreign_keys} == {
        "memory_unit.id"
    }
    assert {foreign_key.target_fullname for foreign_key in edge.c.to_memory_id.foreign_keys} == {
        "memory_unit.id"
    }
    queue_item = Base.metadata.tables["approval_queue_item"]
    assert {
        foreign_key.target_fullname for foreign_key in queue_item.c.candidate_memory_id.foreign_keys
    } == {"memory_unit.id"}
    decision = Base.metadata.tables["approval_decision"]
    assert {foreign_key.target_fullname for foreign_key in decision.c.item_uid.foreign_keys} == {
        "approval_queue_item.item_uid"
    }
    annotation = Base.metadata.tables["injection_event_annotation"]
    assert {
        foreign_key.target_fullname for foreign_key in annotation.c.target_event_uid.foreign_keys
    } == {"injection_event.event_uid"}
    learner_run = Base.metadata.tables["learner_run"]
    assert {
        foreign_key.target_fullname for foreign_key in learner_run.c.incumbent_version.foreign_keys
    } == {"scorer_config.version"}
    assert {
        foreign_key.target_fullname for foreign_key in learner_run.c.proposal_version.foreign_keys
    } == {"scorer_config.version"}
    learner_indexes = {index.name: index for index in learner_run.indexes}
    assert set(learner_indexes) == {"learner_run_ts_idx"}

    optimization_run = Base.metadata.tables["optimization_run"]
    assert {
        foreign_key.target_fullname
        for foreign_key in optimization_run.c.incumbent_version.foreign_keys
    } == {"scorer_config.version"}
    assert {
        foreign_key.target_fullname
        for foreign_key in optimization_run.c.challenger_version.foreign_keys
    } == {"scorer_config.version"}
    optimization_indexes = {index.name: index for index in optimization_run.indexes}
    assert set(optimization_indexes) == {
        "optimization_run_loop_completed_idx",
        "optimization_run_trigger_thread_idx",
    }
    assert (
        str(
            optimization_indexes["optimization_run_trigger_thread_idx"].dialect_options[
                "postgresql"
            ]["where"]
        )
        == "trigger_thread_id IS NOT NULL"
    )

    adoption = Base.metadata.tables["optimization_run_adoption"]
    assert {foreign_key.target_fullname for foreign_key in adoption.c.run_uid.foreign_keys} == {
        "optimization_run.run_uid"
    }
    assert {
        foreign_key.target_fullname for foreign_key in adoption.c.tap_event_uid.foreign_keys
    } == {"scorer_activation.event_uid"}


async def test_search_tsv_trigger_tracks_sources_and_overwrites_tampering(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """SPEC C.2 is defended by verifying that search tsv trigger tracks sources and overwrites
    tampering; this prevents drift in the authoritative revisioned storage contract.
    """
    _, sessions = database
    memory_id = uuid4()
    await _seed_memory(
        sessions,
        memory_id=memory_id,
        root_uid=_rev_uid(91),
        principal_id=f"fts-trigger-{memory_id}",
        label="InitialLabelNeedle",
        body="InitialBodyNeedle",
        keywords=("InitialKeywordNeedle",),
    )

    async with sessions() as session:
        for term in ("InitialLabelNeedle", "InitialBodyNeedle", "InitialKeywordNeedle"):
            assert await _search_tsv_matches(session, memory_id, term)

    async with sessions.begin() as session:
        await session.execute(
            update(MemoryUnit)
            .where(MemoryUnit.id == memory_id)
            .values(
                label="ChangedLabelNeedle",
                body="ChangedBodyNeedle",
                keywords=["ChangedKeywordNeedle"],
            )
        )
        await session.execute(
            update(MemoryUnit)
            .where(MemoryUnit.id == memory_id)
            .values(
                search_tsv=func.to_tsvector(
                    text("'pg_catalog.simple'::regconfig"),
                    "TamperNeedle",
                )
            )
        )

    async with sessions() as session:
        for term in ("ChangedLabelNeedle", "ChangedBodyNeedle", "ChangedKeywordNeedle"):
            assert await _search_tsv_matches(session, memory_id, term)
        for term in (
            "InitialLabelNeedle",
            "InitialBodyNeedle",
            "InitialKeywordNeedle",
            "TamperNeedle",
        ):
            assert not await _search_tsv_matches(session, memory_id, term)


async def test_cas_updates_form_cloud_head_lineage(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """SPEC C.2 is defended by verifying that cas updates form cloud head lineage; this
    prevents drift in the authoritative revisioned storage contract.
    """
    _, sessions = database
    memory_id = uuid4()
    root_uid, child_uid, grandchild_uid = (_rev_uid(value) for value in (101, 102, 103))
    await _seed_memory(
        sessions,
        memory_id=memory_id,
        root_uid=root_uid,
        principal_id=f"lineage-{memory_id}",
        label="original label",
    )

    async with sessions.begin() as session:
        child = await cas_update_memory_unit(
            session,
            CasUpdate(
                memory_id=memory_id,
                expected_revision=1,
                rev_uid=child_uid,
                editor="agent:writer",
                origin_machine_id="machine-child",
                reason="body correction",
                changes=MemoryUnitChanges(
                    body="child body",
                    origin_path="src/spine/db",
                ),
            ),
        )
    async with sessions.begin() as session:
        grandchild = await cas_update_memory_unit(
            session,
            CasUpdate(
                memory_id=memory_id,
                expected_revision=2,
                rev_uid=grandchild_uid,
                editor="user",
                origin_machine_id="machine-grandchild",
                reason="rename",
                changes=MemoryUnitChanges(label="renamed label"),
            ),
        )

    assert child.revision == 2
    assert child.body == "child body"
    assert child.label == "original label"
    assert child.origin_path == "src/spine/db"
    assert grandchild.revision == 3
    assert grandchild.body == "child body"
    assert grandchild.label == "renamed label"
    assert grandchild.origin_path == "src/spine/db"
    assert grandchild.updated_at > child.updated_at > child.created_at

    async with sessions() as session:
        revisions = (
            await session.scalars(
                select(MemoryRevision)
                .where(MemoryRevision.memory_id == memory_id)
                .order_by(MemoryRevision.revision)
            )
        ).all()
    assert [revision.rev_uid for revision in revisions] == [root_uid, child_uid, grandchild_uid]
    assert [revision.parent_uid for revision in revisions] == [None, root_uid, child_uid]
    assert [revision.revision for revision in revisions] == [1, 2, 3]
    assert [(revision.body, revision.label) for revision in revisions] == [
        ("root body", "original label"),
        ("child body", "original label"),
        ("child body", "renamed label"),
    ]
    assert (revisions[1].editor, revisions[1].origin_machine_id, revisions[1].reason) == (
        "agent:writer",
        "machine-child",
        "body correction",
    )


async def test_competing_cas_has_one_winner_and_current_409(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """SPEC C.2 is defended by verifying that competing cas has one winner and current 409;
    this prevents drift in the authoritative revisioned storage contract.
    """
    _, sessions = database
    memory_id = uuid4()
    root_uid = _rev_uid(201)
    await _seed_memory(
        sessions,
        memory_id=memory_id,
        root_uid=root_uid,
        principal_id=f"race-{memory_id}",
        label="race label",
    )

    ready = asyncio.Event()
    ready_lock = asyncio.Lock()
    ready_count = 0

    async def contender(body: str, rev_uid: str) -> MemoryUnitSnapshot:
        nonlocal ready_count
        async with sessions.begin() as session:
            async with ready_lock:
                ready_count += 1
                if ready_count == 2:
                    ready.set()
            await ready.wait()
            return await cas_update_memory_unit(
                session,
                CasUpdate(
                    memory_id=memory_id,
                    expected_revision=1,
                    rev_uid=rev_uid,
                    editor="agent:race",
                    origin_machine_id="machine-race",
                    changes=MemoryUnitChanges(body=body),
                ),
            )

    results = await asyncio.gather(
        contender("writer A", _rev_uid(202)),
        contender("writer B", _rev_uid(203)),
        return_exceptions=True,
    )
    winners = [result for result in results if isinstance(result, MemoryUnitSnapshot)]
    conflicts = [result for result in results if isinstance(result, MemoryCasConflictError)]

    assert len(winners) == 1
    assert len(conflicts) == 1
    assert conflicts[0].status_code == 409
    assert conflicts[0].current.revision == 2
    assert conflicts[0].current.body == winners[0].body

    async with sessions() as session:
        head = await session.get(MemoryUnit, memory_id)
        revision_count = await session.scalar(
            select(func.count())
            .select_from(MemoryRevision)
            .where(MemoryRevision.memory_id == memory_id)
        )
    assert head is not None
    assert head.revision == 2
    assert head.body == winners[0].body
    assert revision_count == 2


async def test_revision_append_failure_rolls_back_head_update(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """SPEC C.2 is defended by verifying that revision append failure rolls back head update;
    this prevents drift in the authoritative revisioned storage contract.
    """
    _, sessions = database
    memory_id = uuid4()
    root_uid = _rev_uid(301)
    await _seed_memory(
        sessions,
        memory_id=memory_id,
        root_uid=root_uid,
        principal_id=f"rollback-{memory_id}",
        label="rollback label",
    )

    with pytest.raises(IntegrityError):
        async with sessions.begin() as session:
            await cas_update_memory_unit(
                session,
                CasUpdate(
                    memory_id=memory_id,
                    expected_revision=1,
                    rev_uid=root_uid,
                    editor="agent:broken",
                    origin_machine_id="machine-broken",
                    changes=MemoryUnitChanges(body="must roll back"),
                ),
            )

    async with sessions() as session:
        head = await session.get(MemoryUnit, memory_id)
        revisions = (
            await session.scalars(
                select(MemoryRevision).where(MemoryRevision.memory_id == memory_id)
            )
        ).all()
    assert head is not None
    assert (head.revision, head.body) == (1, "root body")
    assert [revision.rev_uid for revision in revisions] == [root_uid]


async def test_lineage_error_rolls_back_when_caught_inside_outer_transaction(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """SPEC C.2 is defended by verifying that lineage error rolls back when caught inside outer
    transaction; this prevents drift in the authoritative revisioned storage contract.
    """
    _, sessions = database
    memory_id = uuid4()
    async with sessions.begin() as session:
        await session.execute(
            insert(MemoryUnit).values(
                id=memory_id,
                principal_id=f"broken-lineage-{memory_id}",
                label="broken lineage",
                body="unchanged body",
                kind="fact",
                embedding=list(ZERO_EMBEDDING),
                embedding_model="s1-test",
            )
        )
        with pytest.raises(MemoryLineageError):
            await cas_update_memory_unit(
                session,
                CasUpdate(
                    memory_id=memory_id,
                    expected_revision=1,
                    rev_uid=_rev_uid(351),
                    editor="agent:broken-lineage",
                    origin_machine_id="machine-broken-lineage",
                    changes=MemoryUnitChanges(body="must roll back"),
                ),
            )

        head_inside_outer_transaction = await session.get(MemoryUnit, memory_id)
        assert head_inside_outer_transaction is not None
        assert (head_inside_outer_transaction.revision, head_inside_outer_transaction.body) == (
            1,
            "unchanged body",
        )

    async with sessions() as session:
        persisted = await session.get(MemoryUnit, memory_id)
        revision_count = await session.scalar(
            select(func.count())
            .select_from(MemoryRevision)
            .where(MemoryRevision.memory_id == memory_id)
        )
    assert persisted is not None
    assert (persisted.revision, persisted.body) == (1, "unchanged body")
    assert revision_count == 0


async def test_cas_requires_a_caller_owned_transaction(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """SPEC C.2 is defended by verifying that cas requires a caller owned transaction; this
    prevents drift in the authoritative revisioned storage contract.
    """
    _, sessions = database
    memory_id = uuid4()
    root_uid = _rev_uid(352)
    await _seed_memory(
        sessions,
        memory_id=memory_id,
        root_uid=root_uid,
        principal_id=f"transaction-{memory_id}",
        label="transaction boundary",
    )
    command = CasUpdate(
        memory_id=memory_id,
        expected_revision=1,
        rev_uid=_rev_uid(353),
        editor="agent:transaction",
        origin_machine_id="machine-transaction",
        changes=MemoryUnitChanges(body="rolled back by caller"),
    )

    async with sessions() as session:
        with pytest.raises(RuntimeError, match="explicit caller transaction"):
            await cas_update_memory_unit(session, command)
        await session.scalar(select(1))
        with pytest.raises(RuntimeError, match="explicit caller transaction"):
            await cas_update_memory_unit(session, command)
        await session.rollback()

    async with sessions() as session:
        transaction = await session.begin()
        changed = await cas_update_memory_unit(session, command)
        assert (changed.revision, changed.body) == (2, "rolled back by caller")
        await transaction.rollback()

    async with sessions() as session:
        persisted = await session.get(MemoryUnit, memory_id)
        revision_count = await session.scalar(
            select(func.count())
            .select_from(MemoryRevision)
            .where(MemoryRevision.memory_id == memory_id)
        )
    assert persisted is not None
    assert (persisted.revision, persisted.body) == (1, "root body")
    assert revision_count == 1


def test_cas_command_requires_a_canonical_ulid() -> None:
    """SPEC C.2 is defended by verifying that cas command requires a canonical ulid; this
    prevents drift in the authoritative revisioned storage contract.
    """
    with pytest.raises(ValueError, match="canonical 26-character ULID"):
        CasUpdate(
            memory_id=uuid4(),
            expected_revision=1,
            rev_uid="not-a-ulid",
            editor="agent:invalid",
            origin_machine_id="machine-invalid",
            changes=MemoryUnitChanges(body="not written"),
        )


async def test_tombstone_is_revisioned_and_releases_active_label(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """SPEC C.2 is defended by verifying that tombstone is revisioned and releases active
    label; this prevents drift in the authoritative revisioned storage contract.
    """
    _, sessions = database
    memory_id = uuid4()
    replacement_id = uuid4()
    root_uid = _rev_uid(401)
    principal_id = f"tombstone-{memory_id}"
    label = "reusable label"
    await _seed_memory(
        sessions,
        memory_id=memory_id,
        root_uid=root_uid,
        principal_id=principal_id,
        label=label,
    )

    async with sessions.begin() as session:
        tombstone = await tombstone_memory_unit(
            session,
            memory_id=memory_id,
            expected_revision=1,
            rev_uid=_rev_uid(402),
            editor="user",
            origin_machine_id="machine-tombstone",
            reason="remove",
        )
    async with sessions.begin() as session:
        await _insert_memory_and_root(
            session,
            memory_id=replacement_id,
            root_uid=_rev_uid(403),
            principal_id=principal_id,
            label=label,
            body="replacement body",
        )

    assert tombstone.status == "tombstoned"
    assert tombstone.revision == 2
    async with sessions() as session:
        original = await session.get(MemoryUnit, memory_id)
        replacement = await session.get(MemoryUnit, replacement_id)
        revisions = (
            await session.scalars(
                select(MemoryRevision)
                .where(MemoryRevision.memory_id == memory_id)
                .order_by(MemoryRevision.revision)
            )
        ).all()
    assert original is not None
    assert (original.status, original.revision) == ("tombstoned", 2)
    assert replacement is not None
    assert replacement.status == "active"
    assert [revision.parent_uid for revision in revisions] == [None, root_uid]
    assert [revision.revision for revision in revisions] == [1, 2]
