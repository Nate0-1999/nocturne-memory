"""Transactional implementation of SPEC C.4 ``inject/prepare``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spine.contracts import MemoryAllocation, MemoryFeatures, PrepareResponse, ScoredMemoryCard
from spine.db.memory import (
    CasUpdate,
    MemoryCasConflictError,
    MemoryUnitChanges,
    cas_update_memory_unit,
)
from spine.db.models import (
    InjectionEvent,
    MemoryRevision,
    MemoryUnit,
    Thread,
)
from spine.db.models import (
    ScorerConfig as DatabaseScorerConfig,
)
from spine.embeddings import (
    EmbeddingConfigurationError,
    EmbeddingProvider,
    EmbeddingReceiptContext,
    embed_one,
)
from spine.ids import mint_ulid
from spine.inject.renderer import render_final_block
from spine.inject.scorer import (
    ScoredCandidate,
    ScorerConfig,
    ScoringCandidate,
    ScoringSelection,
    prompt_keywords,
    score_and_select,
)

_EMBEDDING_DIMENSIONS = 1536
_FTS_CANDIDATE_POOL = 50
_MAX_TRANSACTION_ATTEMPTS = 3


@dataclass(frozen=True, slots=True, kw_only=True)
class PrepareCommand:
    thread_id: UUID
    agent_id: str
    machine_id: str
    principal_id: str
    project_key: str | None
    location_path: str | None = None
    current_location: str | None = None
    agent_kind: str
    prompt: str
    model_context_tokens: int
    mode: Literal["gate", "autonomous"] = "gate"
    current_memory_ids: tuple[UUID, ...] = ()
    confirmed_memory_ids: tuple[UUID, ...] = ()
    excluded_memory_ids: tuple[UUID, ...] = ()


class PrepareServiceError(RuntimeError):
    """Base class for expected prepare-domain failures."""


class ThreadAlreadyPreparedError(PrepareServiceError):
    """M1 permits one successful memory injection per thread."""


class ThreadIdentityConflictError(PrepareServiceError):
    """A thread UUID is already owned by different identity metadata."""


class InvalidRescoreStateError(PrepareServiceError):
    """An autonomous request cannot preserve its declared thread locks."""


class PrepareConflictError(PrepareServiceError):
    """Concurrent writes repeatedly prevented one atomic snapshot."""


class ScorerConfigurationError(PrepareServiceError):
    """The database does not identify exactly one valid active scorer."""


class _RetryTransaction(RuntimeError):
    """An invisible concurrent thread insert requires a fresh MVCC snapshot."""


def _validate_command(command: PrepareCommand) -> None:
    current = set(command.current_memory_ids)
    confirmed = set(command.confirmed_memory_ids)
    excluded = set(command.excluded_memory_ids)
    if len(current) != len(command.current_memory_ids):
        raise InvalidRescoreStateError("current_memory_ids contains duplicates")
    if len(confirmed) != len(command.confirmed_memory_ids):
        raise InvalidRescoreStateError("confirmed_memory_ids contains duplicates")
    if len(excluded) != len(command.excluded_memory_ids):
        raise InvalidRescoreStateError("excluded_memory_ids contains duplicates")
    if command.mode == "gate":
        if current or confirmed or excluded:
            raise InvalidRescoreStateError("gate mode does not accept thread disposition sets")
        return
    if not confirmed <= current:
        raise InvalidRescoreStateError(
            "confirmed_memory_ids must be a subset of current_memory_ids"
        )
    if excluded & (current | confirmed):
        raise InvalidRescoreStateError("excluded_memory_ids must be disjoint from current state")


class PrepareService:
    """Own the one-shot M1 prepare transaction and its audit events."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedding_provider: EmbeddingProvider,
    ) -> None:
        if embedding_provider.dimensions != _EMBEDDING_DIMENSIONS:
            raise EmbeddingConfigurationError(
                f"memory_unit requires {_EMBEDDING_DIMENSIONS}-dimension embeddings; "
                f"provider supplies {embedding_provider.dimensions}"
            )
        self._session_factory = session_factory
        self._embedding_provider = embedding_provider

    async def preview_scores(
        self, command: PrepareCommand, memory_ids: Sequence[UUID]
    ) -> dict[str, float]:
        """M3MP: score visible owned memories without injection or learning events."""
        embedding = await embed_one(
            self._embedding_provider,
            command.prompt,
            expected_dimensions=_EMBEDDING_DIMENSIONS,
            receipt_context=EmbeddingReceiptContext(
                principal_id=command.principal_id,
                machine_id=command.machine_id,
                origin_agent=command.agent_id,
                thread_id=command.thread_id,
            ),
        )
        unit, revision = MemoryUnit.__table__, MemoryRevision.__table__
        edits = (
            select(revision.c.memory_id, func.max(revision.c.ts).label("last_human_edit_at"))
            .where(revision.c.editor == "user")
            .group_by(revision.c.memory_id)
            .subquery()
        )
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                )
                now = await session.scalar(select(func.clock_timestamp()))
                config = await _active_scorer_config(session)
                rows = (
                    (
                        await session.execute(
                            select(*unit.c, edits.c.last_human_edit_at)
                            .select_from(unit.outerjoin(edits, edits.c.memory_id == unit.c.id))
                            .where(
                                unit.c.principal_id == command.principal_id,
                                unit.c.id.in_(memory_ids),
                                unit.c.status == "active",
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
        candidates = [_candidate_from_row(row, pool_sources=("panel",)) for row in rows]
        selection = score_and_select(
            prompt=command.prompt,
            query_embedding=embedding,
            snapshot_ts=now,
            thread_project_key=command.project_key,
            thread_id=command.thread_id,
            location_path=command.location_path,
            current_location=command.current_location,
            pinned_candidates=[item for item in candidates if item.pin],
            regular_candidates=[item for item in candidates if not item.pin],
            model_context_tokens=command.model_context_tokens,
            config=config,
        )
        return {
            str(item.candidate.memory_id): item.score
            for item in (*selection.injected, *selection.unselected)
        }

    async def prepare(self, command: PrepareCommand) -> PrepareResponse:
        """Embed a prompt, freeze one thread snapshot, score, and log atomically."""

        _validate_command(command)

        query_embedding = await embed_one(
            self._embedding_provider,
            command.prompt,
            expected_dimensions=_EMBEDDING_DIMENSIONS,
            receipt_context=EmbeddingReceiptContext(
                principal_id=command.principal_id,
                machine_id=command.machine_id,
                origin_agent=command.agent_id,
                thread_id=command.thread_id,
            ),
        )

        for attempt in range(_MAX_TRANSACTION_ATTEMPTS):
            try:
                return await self._prepare_once(command, query_embedding)
            except _RetryTransaction:
                pass
            except MemoryCasConflictError:
                pass
            except DBAPIError as error:
                if not _is_serialization_failure(error):
                    raise
            if attempt + 1 == _MAX_TRANSACTION_ATTEMPTS:
                break
        raise PrepareConflictError("could not establish a stable prepare snapshot")

    async def _prepare_once(
        self,
        command: PrepareCommand,
        query_embedding: Sequence[float],
    ) -> PrepareResponse:
        async with self._session_factory() as session:
            async with session.begin():
                # This must be the first transaction command. All following reads,
                # event writes, and CAS counters share one frozen MVCC boundary.
                await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
                snapshot_ts = await session.scalar(select(func.clock_timestamp()))
                if snapshot_ts is None:  # pragma: no cover - PostgreSQL invariant
                    raise RuntimeError("database clock returned no snapshot timestamp")

                await _stamp_thread(session, command, snapshot_ts)
                config = await _active_scorer_config(session)
                pinned, regular = await _load_candidates(
                    session,
                    command=command,
                    query_embedding=query_embedding,
                    candidate_pool=config.params.candidate_pool,
                )
                selection = score_and_select(
                    prompt=command.prompt,
                    query_embedding=query_embedding,
                    snapshot_ts=snapshot_ts,
                    thread_project_key=command.project_key,
                    thread_id=command.thread_id,
                    location_path=command.location_path,
                    current_location=command.current_location,
                    pinned_candidates=pinned,
                    regular_candidates=regular,
                    locked_memory_ids=frozenset(command.confirmed_memory_ids),
                    model_context_tokens=command.model_context_tokens,
                    config=config,
                )
                allocation = MemoryAllocation(
                    memory_context_share=config.params.memory_context_share,
                    share_tokens=selection.regular_budget,
                    regular_tokens=selection.regular_token_cost,
                    pinned_tokens=selection.pin_token_cost,
                    total_tokens=selection.total_token_cost,
                    pinned_overflow_tokens=selection.pinned_overflow_tokens,
                )
                injection_id = uuid4()
                await _increment_injection_stats(
                    session,
                    command=command,
                    snapshot_ts=snapshot_ts,
                    injected=selection.injected,
                )
                event_values = await _insert_events(
                    session,
                    command=command,
                    snapshot_ts=snapshot_ts,
                    injection_id=injection_id,
                    scorer_version=config.version,
                    selection=selection,
                    allocation=allocation,
                )

                return PrepareResponse(
                    injection_id=injection_id,
                    snapshot_ts=snapshot_ts,
                    scorer_version=config.version,
                    injected=[_response_card(item) for item in selection.injected],
                    near_misses=[_response_card(item) for item in selection.near_misses],
                    final_block=(
                        render_final_block(
                            [
                                event
                                for event in event_values
                                if event["outcome"] in {"kept", "auto_entered"}
                            ]
                        )
                        if command.mode == "autonomous"
                        else None
                    ),
                    memory_allocation=allocation,
                )


async def _stamp_thread(
    session: AsyncSession,
    command: PrepareCommand,
    snapshot_ts: datetime,
) -> None:
    table = Thread.__table__
    inserted = (
        (
            await session.execute(
                postgresql_insert(table)
                .values(
                    id=command.thread_id,
                    principal_id=command.principal_id,
                    agent_id=command.agent_id,
                    machine_id=command.machine_id,
                    project_key=command.project_key,
                    snapshot_ts=snapshot_ts,
                )
                .on_conflict_do_nothing(index_elements=[table.c.id])
                .returning(table.c.id)
            )
        )
        .scalars()
        .one_or_none()
    )
    if inserted is not None:
        if command.mode == "autonomous":
            raise InvalidRescoreStateError("autonomous mode requires an already prepared thread")
        return

    existing = (
        (
            await session.execute(
                select(*table.c).where(table.c.id == command.thread_id).with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing is None:
        # PostgreSQL uniqueness can observe a row committed after our repeatable-
        # read snapshot even though SELECT cannot. A fresh transaction resolves it.
        raise _RetryTransaction
    if (
        existing["principal_id"] != command.principal_id
        or existing["agent_id"] != command.agent_id
        or existing["machine_id"] != command.machine_id
        or existing["project_key"] != command.project_key
    ):
        raise ThreadIdentityConflictError(str(command.thread_id))
    if command.mode == "gate" and existing["snapshot_ts"] is not None:
        raise ThreadAlreadyPreparedError(str(command.thread_id))
    if command.mode == "autonomous" and existing["snapshot_ts"] is None:
        raise InvalidRescoreStateError("autonomous mode requires a completed gate prepare")
    await session.execute(
        update(table).where(table.c.id == command.thread_id).values(snapshot_ts=snapshot_ts)
    )


async def _active_scorer_config(session: AsyncSession) -> ScorerConfig:
    table = DatabaseScorerConfig.__table__
    rows = (
        (
            await session.execute(
                select(table.c.version, table.c.weights, table.c.params)
                .where(table.c.active.is_(True))
                .order_by(table.c.version.asc())
            )
        )
        .mappings()
        .all()
    )
    if len(rows) != 1:
        raise ScorerConfigurationError(
            f"expected exactly one active scorer_config row; found {len(rows)}"
        )
    row = rows[0]
    try:
        return ScorerConfig.from_mappings(
            version=row["version"],
            weights=row["weights"],
            params=row["params"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ScorerConfigurationError("active scorer_config is invalid") from error


async def _load_candidates(
    session: AsyncSession,
    *,
    command: PrepareCommand,
    query_embedding: Sequence[float],
    candidate_pool: int,
) -> tuple[list[ScoringCandidate], list[ScoringCandidate]]:
    unit = MemoryUnit.__table__
    revision = MemoryRevision.__table__
    human_edits = (
        select(
            revision.c.memory_id,
            func.max(revision.c.ts).label("last_human_edit_at"),
        )
        .where(revision.c.editor == "user")
        .group_by(revision.c.memory_id)
        .subquery()
    )
    columns = (*unit.c, human_edits.c.last_human_edit_at)
    excluded_ids = tuple(command.excluded_memory_ids)
    base_filters: tuple[Any, ...] = (
        unit.c.principal_id == command.principal_id,
        unit.c.status == "active",
        or_(unit.c.project_key.is_(None), unit.c.project_key == command.project_key),
    )
    if excluded_ids:
        base_filters = (*base_filters, unit.c.id.not_in(excluded_ids))
    joined = unit.outerjoin(human_edits, human_edits.c.memory_id == unit.c.id)

    pinned_rows = (
        (
            await session.execute(
                select(*columns)
                .select_from(joined)
                .where(*base_filters, unit.c.pin.is_(True))
                .order_by(unit.c.id.asc())
            )
        )
        .mappings()
        .all()
    )
    distance = unit.c.embedding.cosine_distance(list(query_embedding))
    # C.3 defines an exact cosine/UUID boundary. Materializing the eligible
    # IDs and distances before ordering prevents an approximate HNSW cutoff
    # from changing membership (especially at the 50th-place tie).
    eligible_vectors = (
        select(
            unit.c.id.label("memory_id"),
            distance.label("vector_distance"),
        )
        .where(*base_filters, unit.c.pin.is_(False))
        .cte("eligible_vector_candidates")
        .prefix_with("MATERIALIZED")
    )
    top_vectors = (
        select(
            eligible_vectors.c.memory_id,
            eligible_vectors.c.vector_distance,
        )
        .order_by(
            eligible_vectors.c.vector_distance.asc(),
            eligible_vectors.c.memory_id.asc(),
        )
        .limit(candidate_pool)
        .subquery()
    )
    vector_rows = (
        (
            await session.execute(
                select(*columns)
                .select_from(joined.join(top_vectors, top_vectors.c.memory_id == unit.c.id))
                .order_by(top_vectors.c.vector_distance.asc(), unit.c.id.asc())
            )
        )
        .mappings()
        .all()
    )

    terms = sorted(prompt_keywords(command.prompt))
    fts_rows: Sequence[Mapping[str, Any]] = ()
    if terms:
        fts_query = func.to_tsquery("pg_catalog.simple", " | ".join(terms))
        rank = func.ts_rank_cd(unit.c.search_tsv, fts_query)
        fts_rows = (
            (
                await session.execute(
                    select(*columns)
                    .select_from(joined)
                    .where(
                        *base_filters,
                        unit.c.pin.is_(False),
                        unit.c.search_tsv.op("@@")(fts_query),
                    )
                    .order_by(rank.desc(), unit.c.id.asc())
                    .limit(_FTS_CANDIDATE_POOL)
                )
            )
            .mappings()
            .all()
        )

    merged: dict[UUID, tuple[Mapping[str, Any], list[str]]] = {}
    for source, rows in (("vector", vector_rows), ("fts", fts_rows)):
        for row in rows:
            memory_id = row["id"]
            if memory_id not in merged:
                merged[memory_id] = (row, [])
            merged[memory_id][1].append(source)

    current_ids = set(command.current_memory_ids)
    if current_ids:
        eligible_pinned = {row["id"] for row in pinned_rows if row["id"] in current_ids}
        explicit_rows = (
            (
                await session.execute(
                    select(*columns)
                    .select_from(joined)
                    .where(*base_filters, unit.c.pin.is_(False), unit.c.id.in_(current_ids))
                    .order_by(unit.c.id.asc())
                )
            )
            .mappings()
            .all()
        )
        found = eligible_pinned | {row["id"] for row in explicit_rows}
        missing = current_ids - found
        if missing:
            memory_id = min(missing, key=lambda value: value.int)
            raise InvalidRescoreStateError(
                f"current memory {memory_id} is not active and eligible for this thread"
            )
        for row in explicit_rows:
            memory_id = row["id"]
            if memory_id not in merged:
                merged[memory_id] = (row, [])
            if "current" not in merged[memory_id][1]:
                merged[memory_id][1].append("current")

    return (
        [_candidate_from_row(row, pool_sources=("pinned",)) for row in pinned_rows],
        [
            _candidate_from_row(row, pool_sources=tuple(sources))
            for _, (row, sources) in sorted(merged.items(), key=lambda item: item[0].int)
        ],
    )


def _candidate_from_row(
    row: Mapping[str, Any],
    *,
    pool_sources: tuple[str, ...],
) -> ScoringCandidate:
    return ScoringCandidate(
        memory_id=row["id"],
        label=row["label"],
        body=row["body"],
        kind=row["kind"],
        keywords=tuple(row["keywords"]),
        embedding=tuple(float(value) for value in row["embedding"]),
        project_key=row["project_key"],
        origin_thread_id=row["origin_thread_id"],
        origin_path=row["origin_path"],
        origin_location=row["origin_location"],
        pin=row["pin"],
        updated_at=row["updated_at"],
        last_human_edit_at=row["last_human_edit_at"],
        stats=deepcopy(row["stats"]),
        bias=float(row["bias"]),
        revision=row["revision"],
        pool_sources=pool_sources,
    )


async def _increment_injection_stats(
    session: AsyncSession,
    *,
    command: PrepareCommand,
    snapshot_ts: datetime,
    injected: Sequence[ScoredCandidate],
) -> None:
    for item in sorted(injected, key=lambda scored: scored.candidate.memory_id.int):
        candidate = item.candidate
        stats = deepcopy(dict(candidate.stats))
        injections = stats.get("injections", 0)
        if isinstance(injections, bool) or not isinstance(injections, int):
            raise ScorerConfigurationError("memory stats.injections must be an integer")
        stats["injections"] = injections + 1
        stats["last_injected_at"] = snapshot_ts.isoformat()
        await cas_update_memory_unit(
            session,
            CasUpdate(
                memory_id=candidate.memory_id,
                expected_revision=candidate.revision,
                rev_uid=mint_ulid(),
                editor="system:inject",
                origin_machine_id=command.machine_id,
                reason="inject/prepare",
                changes=MemoryUnitChanges(stats=stats),
            ),
        )


async def _insert_events(
    session: AsyncSession,
    *,
    command: PrepareCommand,
    snapshot_ts: datetime,
    injection_id: UUID,
    scorer_version: str,
    selection: ScoringSelection,
    allocation: MemoryAllocation,
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    current_ids = set(command.current_memory_ids)
    recorded_ids: set[UUID] = set()
    for item in selection.injected:
        recorded_ids.add(item.candidate.memory_id)
        values.append(
            _event_values(
                command=command,
                snapshot_ts=snapshot_ts,
                injection_id=injection_id,
                scorer_version=scorer_version,
                item=item,
                shown_as="pinned" if item.candidate.pin else "injected",
                actor_class="passive" if command.mode == "autonomous" else "human",
                outcome=("kept" if item.candidate.memory_id in current_ids else "auto_entered")
                if command.mode == "autonomous"
                else None,
                allocation=allocation,
            )
        )
    for item in selection.near_misses:
        recorded_ids.add(item.candidate.memory_id)
        values.append(
            _event_values(
                command=command,
                snapshot_ts=snapshot_ts,
                injection_id=injection_id,
                scorer_version=scorer_version,
                item=item,
                shown_as="near_miss",
                actor_class="passive" if command.mode == "autonomous" else "human",
                outcome=(
                    "auto_exited"
                    if command.mode == "autonomous" and item.candidate.memory_id in current_ids
                    else None
                ),
                allocation=allocation,
            )
        )
    for item in selection.budget_cuts:
        values.append(
            _event_values(
                command=command,
                snapshot_ts=snapshot_ts,
                injection_id=injection_id,
                scorer_version=scorer_version,
                item=item,
                shown_as="budget_cut",
                actor_class="passive" if command.mode == "autonomous" else "human",
                outcome="budget_cut",
                allocation=allocation,
            )
        )
    if command.mode == "autonomous":
        for item in selection.unselected:
            if item.candidate.memory_id not in current_ids - recorded_ids:
                continue
            recorded_ids.add(item.candidate.memory_id)
            values.append(
                _event_values(
                    command=command,
                    snapshot_ts=snapshot_ts,
                    injection_id=injection_id,
                    scorer_version=scorer_version,
                    item=item,
                    shown_as="near_miss",
                    actor_class="passive",
                    outcome="auto_exited",
                    allocation=allocation,
                )
            )
    if values:
        await session.execute(insert(InjectionEvent.__table__), values)
    return values


def _event_values(
    *,
    command: PrepareCommand,
    snapshot_ts: datetime,
    injection_id: UUID,
    scorer_version: str,
    item: ScoredCandidate,
    shown_as: str,
    actor_class: Literal["human", "passive"],
    outcome: str | None,
    allocation: MemoryAllocation,
) -> dict[str, Any]:
    candidate = item.candidate
    features: dict[str, Any] = asdict(item.features)
    features["_memory"] = {
        "label": candidate.label,
        "body": candidate.body,
        "pin": candidate.pin,
        "updated_at": candidate.updated_at.isoformat(),
    }
    features["_prepare"] = {
        "model_context_tokens": command.model_context_tokens,
        **allocation.model_dump(mode="python"),
    }
    features["_location"] = {
        "cwd": command.location_path,
        "where": command.current_location,
    }
    features["_retrieval"] = {"sources": list(candidate.pool_sources)}
    return {
        "event_uid": mint_ulid(),
        "injection_id": injection_id,
        "thread_id": command.thread_id,
        "agent_id": command.agent_id,
        "machine_id": command.machine_id,
        "principal_id": command.principal_id,
        "project_key": command.project_key,
        "agent_kind": command.agent_kind,
        "prompt_text": command.prompt,
        "scorer_version": scorer_version,
        "memory_id": candidate.memory_id,
        "memory_kind": candidate.kind,
        "features": features,
        "score": item.score,
        "rank": item.rank,
        "shown_as": shown_as,
        "actor_class": actor_class,
        "outcome": outcome,
        "ts": snapshot_ts,
    }


def _response_card(item: ScoredCandidate) -> ScoredMemoryCard:
    candidate = item.candidate
    return ScoredMemoryCard(
        memory_id=candidate.memory_id,
        label=candidate.label,
        body=candidate.body,
        kind=candidate.kind,
        pin=candidate.pin,
        score=item.score,
        features=MemoryFeatures(**asdict(item.features)),
        rank=item.rank,
    )


def _is_serialization_failure(error: DBAPIError) -> bool:
    candidate: object | None = error.orig
    for _ in range(3):
        if getattr(candidate, "sqlstate", None) == "40001":
            return True
        candidate = getattr(candidate, "__cause__", None)
    return False


__all__ = [
    "InvalidRescoreStateError",
    "PrepareCommand",
    "PrepareConflictError",
    "PrepareService",
    "PrepareServiceError",
    "ScorerConfigurationError",
    "ThreadAlreadyPreparedError",
    "ThreadIdentityConflictError",
]
