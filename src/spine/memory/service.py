"""Transactional memory CRUD and dedup behavior for SPEC C.4."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Final, Literal
from uuid import UUID

from sqlalchemy import func, insert, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spine.contracts import (
    MemoryKind,
    MemoryListResponse,
    MemoryStatus,
    SearchResponse,
    SimilarityMemoryCard,
)
from spine.contracts import (
    MemoryUnit as ContractMemoryUnit,
)
from spine.db.memory import (
    CasUpdate,
    MemoryUnitChanges,
    MemoryUnitSnapshot,
    cas_update_memory_unit,
)
from spine.db.memory import (
    MemoryCasConflictError as DatabaseCasConflictError,
)
from spine.db.memory import (
    MemoryUnitNotFoundError as DatabaseMemoryNotFoundError,
)
from spine.db.models import ApprovalQueueItem, MemoryEdge, MemoryRevision, MemoryUnit
from spine.embeddings import (
    EmbeddingConfigurationError,
    EmbeddingProvider,
    EmbeddingReceiptContext,
    embed_one,
)
from spine.ids import mint_ulid
from spine.tokens import cl100k_token_count

_EMBEDDING_DIMENSIONS = 1536
_ACTIVE_LABEL_CONSTRAINT = "memory_unit_active_label"


class UnsetType:
    """Sentinel type for mutable PATCH properties omitted by the caller."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Final = UnsetType()


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateMemoryCommand:
    principal_id: str
    label: str
    body: str
    kind: MemoryKind
    editor: str
    machine_id: str
    keywords: Sequence[str] = ()
    project_key: str | None = None
    thread_origin: str | None = None
    origin_thread_id: UUID | None = None
    origin_path: str | None = None
    origin_location: str | None = None
    force: bool = False
    parent_uid: str | None = None
    revision_reason: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class SplitMemoryChild:
    label: str
    body: str
    keywords: Sequence[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class PreparedSplitChild:
    """Validated, embedded child for one transaction-owned curator split."""

    label: str
    body: str
    keywords: tuple[str, ...]
    embedding: tuple[float, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class SplitMemoryCommand:
    principal_id: str
    source_body: str
    children: Sequence[SplitMemoryChild]
    editor: str
    machine_id: str
    thread_origin: str | None = None
    origin_thread_id: UUID | None = None
    origin_path: str | None = None
    origin_location: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PatchMemoryCommand:
    memory_id: UUID
    expected_revision: int
    editor: str
    reason: str
    machine_id: str
    body: str | None | UnsetType = UNSET
    label: str | None | UnsetType = UNSET
    keywords: Sequence[str] | None | UnsetType = UNSET
    kind: MemoryKind | None | UnsetType = UNSET
    origin_path: str | None | UnsetType = UNSET
    pin: bool | None | UnsetType = UNSET
    status: MemoryStatus | None | UnsetType = UNSET


@dataclass(frozen=True, slots=True, kw_only=True)
class ListMemoriesQuery:
    project_key: str | None = None
    status: MemoryStatus | None = None
    q: str | None = None
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchMemoriesQuery:
    principal_id: str
    query: str
    k: int = 10
    project_key: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryCreated:
    memory: ContractMemoryUnit


@dataclass(frozen=True, slots=True, kw_only=True)
class SimilarMemories:
    similar: tuple[SimilarityMemoryCard, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class MemorySplitCreated:
    source: ContractMemoryUnit
    created: tuple[ContractMemoryUnit, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class CandidateCreated:
    memory: ContractMemoryUnit
    neighbors: tuple[SimilarityMemoryCard, ...]


@dataclass(frozen=True, slots=True)
class SplitSourceCreated:
    memory: ContractMemoryUnit
    revision_uid: str


class MemoryServiceError(RuntimeError):
    """Base class for expected memory-domain failures."""


class LabelConflictError(MemoryServiceError):
    """An ACTIVE unit already owns the resulting principal/label pair."""

    def __init__(self, memory_id: UUID, label: str) -> None:
        self.memory_id = memory_id
        self.label = label
        super().__init__(f"active memory {memory_id} already uses label {label!r}")


class DuplicateMemoryError(MemoryServiceError):
    """A create request is at or above the hard-duplicate threshold."""

    def __init__(self, duplicate_of: SimilarityMemoryCard) -> None:
        self.duplicate_of = duplicate_of
        super().__init__(f"memory {duplicate_of.memory_id} is a hard duplicate")


class MemoryNotFoundError(MemoryServiceError):
    """The requested memory head does not exist."""

    def __init__(self, memory_id: UUID) -> None:
        self.memory_id = memory_id
        super().__init__(f"memory {memory_id} does not exist")


class RevisionConflictError(MemoryServiceError):
    """A PATCH expected a stale cloud-head revision."""

    def __init__(self, current: ContractMemoryUnit) -> None:
        self.current = current
        super().__init__(
            f"memory {current.memory_id} is at revision {current.revision}; PATCH was stale"
        )


class EmptyPatchError(MemoryServiceError):
    """A PATCH contains no non-null mutable properties."""


class InvalidListQueryError(MemoryServiceError):
    """List paging values fall outside the C.4 bounds."""


class InvalidSearchQueryError(MemoryServiceError):
    """Search result count falls outside the enacted C.4 bounds."""


class MemoryValidationError(MemoryServiceError):
    """A memory label or body exceeds an enacted C.2/C.5 limit."""


class StagedMemoryConflictError(MemoryServiceError):
    """A client-minted Symphony memory ID already names different evidence."""


class MemoryService:
    """Own C.4 memory transactions while keeping HTTP concerns in the router."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedding_provider: EmbeddingProvider,
        *,
        dedup_dup: float = 0.92,
        dedup_sim: float = 0.80,
        label_max: int = 64,
        memory_max_tokens: int = 128,
    ) -> None:
        if embedding_provider.dimensions != _EMBEDDING_DIMENSIONS:
            raise EmbeddingConfigurationError(
                f"memory_unit requires {_EMBEDDING_DIMENSIONS}-dimension embeddings; "
                f"provider supplies {embedding_provider.dimensions}"
            )
        if not 0.0 <= dedup_sim < dedup_dup <= 1.0:
            raise ValueError("dedup thresholds must satisfy 0 <= dedup_sim < dedup_dup <= 1")
        if label_max <= 0 or memory_max_tokens <= 0:
            raise ValueError("memory limits must be positive")

        self._session_factory = session_factory
        self._embedding_provider = embedding_provider
        self._dedup_dup = dedup_dup
        self._dedup_sim = dedup_sim
        self._label_max = label_max
        self._memory_max_tokens = memory_max_tokens

    async def create(
        self,
        command: CreateMemoryCommand,
    ) -> MemoryCreated | SimilarMemories:
        """Create a root head/revision or return the exact dedup-band outcome."""

        self._validate_label(command.label)

        # C.4 deliberately puts the cheap active-label check before provider I/O.
        async with self._session_factory() as preflight_session:
            conflict = await _find_active_label(
                preflight_session,
                principal_id=command.principal_id,
                label=command.label,
            )
        if conflict is not None:
            raise LabelConflictError(conflict["id"], conflict["label"])

        self._validate_body(command.body)
        embedding = await embed_one(
            self._embedding_provider,
            command.body,
            expected_dimensions=_EMBEDDING_DIMENSIONS,
            receipt_context=EmbeddingReceiptContext(
                principal_id=command.principal_id,
                machine_id=command.machine_id,
                origin_agent=_agent_from_editor(command.editor),
                thread_id=_optional_uuid(command.thread_origin),
            ),
        )

        async with self._session_factory() as session:
            async with session.begin():
                # Serialize the authoritative recheck, dedup read, and root insert for
                # one principal. Different principals remain independent.
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:principal_id, 0))"),
                    {"principal_id": command.principal_id},
                )

                conflict = await _find_active_label(
                    session,
                    principal_id=command.principal_id,
                    label=command.label,
                )
                if conflict is not None:
                    raise LabelConflictError(conflict["id"], conflict["label"])

                matches = await self._dedup_matches(
                    session,
                    principal_id=command.principal_id,
                    embedding=embedding,
                )
                highest_score = matches[0].score if matches else None
                band = _classify_dedup_score(
                    highest_score,
                    dedup_sim=self._dedup_sim,
                    dedup_dup=self._dedup_dup,
                )
                if band == "duplicate":
                    raise DuplicateMemoryError(matches[0])
                if band == "similar" and not command.force:
                    return SimilarMemories(similar=tuple(matches))

                try:
                    async with session.begin_nested():
                        row = await self._insert_root(session, command, embedding)
                except IntegrityError as error:
                    # The advisory lock closes service-to-service create races. Keep
                    # the partial unique index authoritative for out-of-band writers.
                    if _integrity_constraint_name(error) != _ACTIVE_LABEL_CONSTRAINT:
                        raise
                    conflict = await _find_active_label(
                        session,
                        principal_id=command.principal_id,
                        label=command.label,
                    )
                    if conflict is not None:
                        raise LabelConflictError(conflict["id"], conflict["label"]) from None
                    raise

                return MemoryCreated(memory=contract_memory_from_row(row))

    async def split(
        self,
        command: SplitMemoryCommand,
    ) -> MemorySplitCreated | SimilarMemories:
        """Create one exact split family or return its first create-band refusal."""

        self._validate_split(command)

        # Preserve C.4's cheap label-before-provider ordering for every child.
        # The principal-locked pass below remains authoritative against races.
        async with self._session_factory() as preflight_session:
            for child in command.children:
                conflict = await _find_active_label(
                    preflight_session,
                    principal_id=command.principal_id,
                    label=child.label,
                )
                if conflict is not None:
                    raise LabelConflictError(conflict["id"], conflict["label"])

        receipt_context = EmbeddingReceiptContext(
            principal_id=command.principal_id,
            machine_id=command.machine_id,
            origin_agent=_agent_from_editor(command.editor),
            thread_id=_optional_uuid(command.thread_origin),
        )
        source_embedding = await embed_one(
            self._embedding_provider,
            command.source_body,
            expected_dimensions=_EMBEDDING_DIMENSIONS,
            receipt_context=receipt_context,
        )
        child_embeddings = [
            await embed_one(
                self._embedding_provider,
                child.body,
                expected_dimensions=_EMBEDDING_DIMENSIONS,
                receipt_context=receipt_context,
            )
            for child in command.children
        ]

        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:principal_id, 0))"),
                    {"principal_id": command.principal_id},
                )

                # No family row exists yet: every comparison below is against the
                # pre-existing ACTIVE corpus, never another semantic sibling.
                for child, embedding in zip(command.children, child_embeddings, strict=True):
                    conflict = await _find_active_label(
                        session,
                        principal_id=command.principal_id,
                        label=child.label,
                    )
                    if conflict is not None:
                        raise LabelConflictError(conflict["id"], conflict["label"])

                    matches = await self._dedup_matches(
                        session,
                        principal_id=command.principal_id,
                        embedding=embedding,
                    )
                    highest_score = matches[0].score if matches else None
                    band = _classify_dedup_score(
                        highest_score,
                        dedup_sim=self._dedup_sim,
                        dedup_dup=self._dedup_dup,
                    )
                    if band == "duplicate":
                        raise DuplicateMemoryError(matches[0])
                    if band == "similar":
                        return SimilarMemories(similar=tuple(matches))

                source_command = CreateMemoryCommand(
                    principal_id=command.principal_id,
                    label="Split source",
                    body=command.source_body,
                    kind="fact",
                    keywords=("split", "source"),
                    project_key=None,
                    thread_origin=command.thread_origin,
                    origin_thread_id=command.origin_thread_id,
                    origin_path=command.origin_path,
                    origin_location=command.origin_location,
                    editor=command.editor,
                    machine_id=command.machine_id,
                    revision_reason="remember/source-split",
                )
                try:
                    async with session.begin_nested():
                        source_row = await self._insert_root(
                            session,
                            source_command,
                            source_embedding,
                            status="tombstoned",
                        )
                        source_revision_uid = await session.scalar(
                            select(MemoryRevision.rev_uid).where(
                                MemoryRevision.memory_id == source_row["id"],
                                MemoryRevision.revision == 1,
                            )
                        )
                        if source_revision_uid is None:  # pragma: no cover - insert invariant
                            raise RuntimeError("split source revision was not written")

                        child_rows: list[Mapping[str, Any]] = []
                        for child, embedding in zip(
                            command.children,
                            child_embeddings,
                            strict=True,
                        ):
                            child_rows.append(
                                await self._insert_root(
                                    session,
                                    CreateMemoryCommand(
                                        principal_id=command.principal_id,
                                        label=child.label,
                                        body=child.body,
                                        kind="fact",
                                        keywords=child.keywords,
                                        project_key=None,
                                        thread_origin=command.thread_origin,
                                        origin_thread_id=command.origin_thread_id,
                                        origin_path=command.origin_path,
                                        origin_location=command.origin_location,
                                        editor=command.editor,
                                        machine_id=command.machine_id,
                                        parent_uid=source_revision_uid,
                                        revision_reason="remember/split-child",
                                    ),
                                    embedding,
                                )
                            )

                        child_ids = [row["id"] for row in child_rows]
                        for left in child_ids:
                            for right in child_ids:
                                if left == right:
                                    continue
                                await session.execute(
                                    insert(MemoryEdge.__table__).values(
                                        edge_uid=mint_ulid(),
                                        from_memory_id=left,
                                        to_memory_id=right,
                                        edge_type="relates_to",
                                    )
                                )
                except IntegrityError as error:
                    if _integrity_constraint_name(error) != _ACTIVE_LABEL_CONSTRAINT:
                        raise
                    for child in command.children:
                        conflict = await _find_active_label(
                            session,
                            principal_id=command.principal_id,
                            label=child.label,
                        )
                        if conflict is not None:
                            raise LabelConflictError(conflict["id"], conflict["label"]) from None
                    raise

                return MemorySplitCreated(
                    source=contract_memory_from_row(source_row),
                    created=tuple(contract_memory_from_row(row) for row in child_rows),
                )

    async def create_candidate(
        self,
        command: CreateMemoryCommand,
    ) -> CandidateCreated | None:
        """Admit a queue-only head, remembering exact rejected bodies as well."""

        self._validate_label(command.label)
        self._validate_body(command.body)
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:principal_id, 0))"),
                    {"principal_id": command.principal_id},
                )
                rejected = await session.scalar(
                    select(MemoryUnit.id)
                    .join(ApprovalQueueItem, ApprovalQueueItem.candidate_memory_id == MemoryUnit.id)
                    .where(
                        MemoryUnit.principal_id == command.principal_id,
                        ApprovalQueueItem.principal_id == command.principal_id,
                        ApprovalQueueItem.state == "rejected",
                        MemoryUnit.status == "tombstoned",
                        func.sha256(func.convert_to(MemoryUnit.body, "UTF8"))
                        == sha256(command.body.encode()).digest(),
                        MemoryUnit.body == command.body,
                    )
                    .limit(1)
                )
                if rejected is not None:
                    return None
                embedding = await embed_one(
                    self._embedding_provider,
                    command.body,
                    expected_dimensions=_EMBEDDING_DIMENSIONS,
                    receipt_context=EmbeddingReceiptContext(
                        principal_id=command.principal_id,
                        machine_id=command.machine_id,
                        origin_agent=_agent_from_editor(command.editor),
                        thread_id=_optional_uuid(command.thread_origin),
                    ),
                )
                matches = await self._dedup_matches(
                    session,
                    principal_id=command.principal_id,
                    embedding=embedding,
                    statuses=("active", "candidate"),
                )
                if matches and matches[0].score >= self._dedup_dup:
                    return None
                row = await self._insert_root(session, command, embedding, status="candidate")
                return CandidateCreated(
                    memory=contract_memory_from_row(row),
                    neighbors=tuple(matches),
                )

    async def create_curator_candidate(
        self,
        command: CreateMemoryCommand,
        *,
        exclude_memory_ids: Sequence[UUID],
    ) -> CandidateCreated | None:
        """Create a replacement candidate without deduping against its named sources."""

        self._validate_label(command.label)
        self._validate_body(command.body)
        embedding = await embed_one(
            self._embedding_provider,
            command.body,
            expected_dimensions=_EMBEDDING_DIMENSIONS,
            receipt_context=EmbeddingReceiptContext(
                principal_id=command.principal_id,
                machine_id=command.machine_id,
                origin_agent="maintenance",
            ),
        )
        excluded = tuple(exclude_memory_ids)
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:principal_id, 0))"),
                    {"principal_id": command.principal_id},
                )
                matches = await self._dedup_matches(
                    session,
                    principal_id=command.principal_id,
                    embedding=embedding,
                    statuses=("active", "candidate"),
                    exclude_memory_ids=excluded,
                )
                if matches and matches[0].score >= self._dedup_dup:
                    return None
                row = await self._insert_root(session, command, embedding, status="candidate")
                return CandidateCreated(
                    memory=contract_memory_from_row(row),
                    neighbors=tuple(matches),
                )

    async def prepare_curator_split(
        self,
        *,
        principal_id: str,
        machine_id: str,
        children: Sequence[SplitMemoryChild],
    ) -> tuple[PreparedSplitChild, ...]:
        """Validate and embed split children before the owner-decision transaction."""

        if not 2 <= len(children) <= 64:
            raise MemoryValidationError("a curator split requires 2 to 64 children")
        prepared: list[PreparedSplitChild] = []
        labels: set[str] = set()
        bodies: set[str] = set()
        for child in children:
            self._validate_label(child.label)
            self._validate_body(child.body)
            keywords = tuple(child.keywords)
            self._validate_split_keywords(keywords)
            if child.label in labels or child.body in bodies:
                raise MemoryValidationError(
                    "curator split children must have distinct labels and bodies"
                )
            labels.add(child.label)
            bodies.add(child.body)
            embedding = await embed_one(
                self._embedding_provider,
                child.body,
                expected_dimensions=_EMBEDDING_DIMENSIONS,
                receipt_context=EmbeddingReceiptContext(
                    principal_id=principal_id,
                    machine_id=machine_id,
                    origin_agent="maintenance",
                ),
            )
            prepared.append(
                PreparedSplitChild(
                    label=child.label,
                    body=child.body,
                    keywords=keywords,
                    embedding=tuple(embedding),
                )
            )
        return tuple(prepared)

    async def enact_curator_split(
        self,
        session: AsyncSession,
        *,
        source: Mapping[str, Any],
        children: Sequence[PreparedSplitChild],
        machine_id: str,
        finding_uid: str,
    ) -> tuple[UUID, ...]:
        """Revision-tombstone one source and create its lineaged semantic children."""

        if source["status"] != "active":
            raise MemoryValidationError("curator split source is no longer active")
        tombstone_revision_uid = mint_ulid()
        await cas_update_memory_unit(
            session,
            CasUpdate(
                memory_id=source["id"],
                expected_revision=source["revision"],
                rev_uid=tombstone_revision_uid,
                editor="maintenance",
                origin_machine_id=machine_id,
                reason=f"curation/{finding_uid}/split-source",
                changes=MemoryUnitChanges(status="tombstoned"),
            ),
        )
        rows: list[Mapping[str, Any]] = []
        for child in children:
            rows.append(
                await self._insert_root(
                    session,
                    CreateMemoryCommand(
                        principal_id=source["principal_id"],
                        label=child.label,
                        body=child.body,
                        kind=source["kind"],
                        keywords=child.keywords,
                        project_key=source["project_key"],
                        thread_origin=source["thread_origin"],
                        origin_thread_id=source["origin_thread_id"],
                        origin_path=source["origin_path"],
                        origin_location=source["origin_location"],
                        editor="maintenance",
                        machine_id=machine_id,
                        parent_uid=tombstone_revision_uid,
                        revision_reason=f"curation/{finding_uid}/split-child",
                    ),
                    child.embedding,
                )
            )
        ids = tuple(row["id"] for row in rows)
        for left in ids:
            for right in ids:
                if left == right:
                    continue
                await session.execute(
                    insert(MemoryEdge.__table__).values(
                        edge_uid=mint_ulid(),
                        from_memory_id=left,
                        to_memory_id=right,
                        edge_type="relates_to",
                    )
                )
        return ids

    async def stage_symphony(
        self,
        command: CreateMemoryCommand,
        *,
        memory_id: UUID,
        run_id: str,
        origin_agent: str,
    ) -> Mapping[str, Any]:
        """Write or exactly replay one run-private Symphony memory head."""

        self._validate_label(command.label)
        self._validate_body(command.body)
        async with self._session_factory() as session:
            existing = await self._staged_replay_row(session, memory_id, command)
            if existing is not None:
                _require_staged_identity(existing, run_id=run_id, origin_agent=origin_agent)
                return existing

        embedding = await embed_one(
            self._embedding_provider,
            command.body,
            expected_dimensions=_EMBEDDING_DIMENSIONS,
            receipt_context=EmbeddingReceiptContext(
                principal_id=command.principal_id,
                machine_id=command.machine_id,
                origin_agent=origin_agent,
            ),
        )
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:memory_id, 0))"),
                    {"memory_id": str(memory_id)},
                )
                existing = await self._staged_replay_row(session, memory_id, command)
                if existing is not None:
                    _require_staged_identity(existing, run_id=run_id, origin_agent=origin_agent)
                    return existing
                return await self._insert_root(
                    session,
                    command,
                    embedding,
                    status="staged",
                    memory_id=memory_id,
                    run_id=run_id,
                    origin_agent=origin_agent,
                )

    async def create_split_source(
        self,
        command: CreateMemoryCommand,
    ) -> SplitSourceCreated:
        """Persist one exact, queue-invisible source revision for semantic children."""

        self._validate_label(command.label)
        embedding = await embed_one(
            self._embedding_provider,
            command.body,
            expected_dimensions=_EMBEDDING_DIMENSIONS,
            receipt_context=EmbeddingReceiptContext(
                principal_id=command.principal_id,
                machine_id=command.machine_id,
                origin_agent=_agent_from_editor(command.editor),
                thread_id=None,
            ),
        )
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:principal_id, 0))"),
                    {"principal_id": command.principal_id},
                )
                row = await self._insert_root(session, command, embedding, status="tombstoned")
                revision_uid = await session.scalar(
                    select(MemoryRevision.rev_uid).where(
                        MemoryRevision.memory_id == row["id"],
                        MemoryRevision.revision == 1,
                    )
                )
                if revision_uid is None:  # pragma: no cover - same-transaction invariant
                    raise RuntimeError("split source revision was not written")
                return SplitSourceCreated(
                    memory=contract_memory_from_row(row),
                    revision_uid=revision_uid,
                )

    async def patch(self, command: PatchMemoryCommand) -> ContractMemoryUnit:
        """CAS-update a head and append its resulting cloud revision."""

        if not any(
            _provided(value)
            for value in (
                command.body,
                command.label,
                command.keywords,
                command.kind,
                command.origin_path,
                command.pin,
                command.status,
            )
        ):
            raise EmptyPatchError("PATCH requires at least one non-null mutable property")

        # C.2 forbids hard deletion, so this preflight gives A-004's missing-ID
        # response precedence without weakening the later revision CAS.
        async with self._session_factory() as preflight_session:
            current = (
                (
                    await preflight_session.execute(
                        select(
                            MemoryUnit.principal_id,
                            MemoryUnit.body,
                            MemoryUnit.stats,
                        ).where(MemoryUnit.id == command.memory_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
        if current is None:
            raise MemoryNotFoundError(command.memory_id)
        principal_id = current["principal_id"]

        is_reinforcement = command.reason == "remember/reinforce"
        if is_reinforcement and (
            not _provided(command.body)
            or command.body != current["body"]
            or any(
                _provided(value)
                for value in (
                    command.label,
                    command.keywords,
                    command.kind,
                    command.origin_path,
                    command.pin,
                    command.status,
                )
            )
        ):
            raise MemoryValidationError(
                "remember/reinforce requires the unchanged current body and no other changes"
            )

        if _provided(command.body):
            self._validate_body(command.body)
        if _provided(command.label):
            self._validate_label(command.label)

        change_values: dict[str, Any] = {}
        if _provided(command.body):
            embedding = await embed_one(
                self._embedding_provider,
                command.body,
                expected_dimensions=_EMBEDDING_DIMENSIONS,
                receipt_context=EmbeddingReceiptContext(
                    principal_id=principal_id,
                    machine_id=command.machine_id,
                    origin_agent=_agent_from_editor(command.editor),
                    memory_id=command.memory_id,
                ),
            )
            change_values.update(
                body=command.body,
                embedding=embedding,
                embedding_model=self._embedding_provider.model,
            )
        if _provided(command.label):
            change_values["label"] = command.label
        if _provided(command.keywords):
            change_values["keywords"] = tuple(command.keywords)
        if _provided(command.kind):
            change_values["kind"] = command.kind
        if _provided(command.origin_path):
            change_values["origin_path"] = command.origin_path
        if _provided(command.pin):
            change_values["pin"] = command.pin
        if _provided(command.status):
            change_values["status"] = command.status
        if is_reinforcement:
            stats = deepcopy(current["stats"])
            reinforcement_count = stats.get("reinforcements", 0)
            if (
                isinstance(reinforcement_count, bool)
                or not isinstance(reinforcement_count, int)
                or reinforcement_count < 0
            ):
                raise RuntimeError("memory reinforcement count is invalid")
            stats["reinforcements"] = reinforcement_count + 1
            change_values["stats"] = stats

        async with self._session_factory() as session:
            async with session.begin():
                try:
                    snapshot = await cas_update_memory_unit(
                        session,
                        CasUpdate(
                            memory_id=command.memory_id,
                            expected_revision=command.expected_revision,
                            rev_uid=mint_ulid(),
                            editor=command.editor,
                            origin_machine_id=command.machine_id,
                            reason=command.reason,
                            changes=MemoryUnitChanges(**change_values),
                        ),
                    )
                except DatabaseMemoryNotFoundError as error:
                    raise MemoryNotFoundError(error.memory_id) from None
                except DatabaseCasConflictError as error:
                    current = contract_memory_from_snapshot(error.current)
                    raise RevisionConflictError(current) from None
                except IntegrityError as error:
                    if _integrity_constraint_name(error) != _ACTIVE_LABEL_CONSTRAINT:
                        raise
                    conflict = await _patch_label_conflict(session, command, change_values)
                    if conflict is not None:
                        raise LabelConflictError(conflict["id"], conflict["label"]) from None
                    raise

                return contract_memory_from_snapshot(snapshot)

    async def list(self, query: ListMemoriesQuery) -> MemoryListResponse:
        """List heads with literal filters, stable ordering, and filtered count."""

        if isinstance(query.limit, bool) or not 1 <= query.limit <= 200:
            raise InvalidListQueryError("limit must satisfy 1 <= limit <= 200")
        if isinstance(query.offset, bool) or query.offset < 0:
            raise InvalidListQueryError("offset must be greater than or equal to zero")

        unit = MemoryUnit.__table__
        filters = []
        if query.status is None:
            filters.append(unit.c.status.not_in(("candidate", "staged")))
        if query.project_key is not None:
            filters.append(unit.c.project_key == query.project_key)
        if query.status is not None:
            filters.append(unit.c.status == query.status)
        if query.q is not None and (term := query.q.strip()):
            pattern = f"%{_escape_ilike(term)}%"
            filters.append(
                or_(
                    unit.c.label.ilike(pattern, escape="\\"),
                    unit.c.body.ilike(pattern, escape="\\"),
                )
            )

        async with self._session_factory() as session:
            total = await session.scalar(select(func.count()).select_from(unit).where(*filters))
            rows = (
                (
                    await session.execute(
                        select(*unit.c)
                        .where(*filters)
                        .order_by(unit.c.updated_at.desc(), unit.c.id.asc())
                        .limit(query.limit)
                        .offset(query.offset)
                    )
                )
                .mappings()
                .all()
            )

        return MemoryListResponse(
            items=[contract_memory_from_row(row) for row in rows],
            total=int(total or 0),
            limit=query.limit,
            offset=query.offset,
        )

    async def search(self, query: SearchMemoriesQuery) -> SearchResponse:
        """Return current ACTIVE heads ordered by raw cosine similarity."""

        if isinstance(query.k, bool) or not isinstance(query.k, int) or not 1 <= query.k <= 50:
            raise InvalidSearchQueryError("k must satisfy 1 <= k <= 50")

        embedding = await embed_one(
            self._embedding_provider,
            query.query,
            expected_dimensions=_EMBEDDING_DIMENSIONS,
            receipt_context=EmbeddingReceiptContext(principal_id=query.principal_id),
        )
        unit = MemoryUnit.__table__
        distance = unit.c.embedding.cosine_distance(list(embedding))
        cosine_score = (1.0 - distance).label("score")
        filters = [
            unit.c.principal_id == query.principal_id,
            unit.c.status == "active",
        ]
        if query.project_key is not None:
            filters.append(
                or_(
                    unit.c.project_key.is_(None),
                    unit.c.project_key == query.project_key,
                )
            )

        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(*unit.c, cosine_score)
                        .where(*filters)
                        .order_by(distance.asc(), unit.c.id.asc())
                        .limit(query.k)
                    )
                )
                .mappings()
                .all()
            )

        return SearchResponse(results=[_similarity_card_from_row(row) for row in rows])

    def _validate_label(self, label: str) -> None:
        if len(label) > self._label_max:
            raise MemoryValidationError(
                f"label has {len(label)} Unicode code points; maximum is {self._label_max}"
            )

    def _validate_body(self, body: str) -> None:
        tokens = cl100k_token_count(body)
        if tokens > self._memory_max_tokens:
            raise MemoryValidationError(
                f"body has {tokens} cl100k_base tokens; maximum is {self._memory_max_tokens}"
            )

    def _validate_split(self, command: SplitMemoryCommand) -> None:
        if not command.source_body.strip():
            raise MemoryValidationError("split source_body must be nonblank")
        if not 2 <= len(command.children) <= 64:
            raise MemoryValidationError("memory split requires between 2 and 64 children")
        self._validate_label("Split source")

        labels: set[str] = set()
        bodies: set[str] = set()
        for child in command.children:
            if (
                not child.label.strip()
                or child.label != child.label.strip()
                or "\n" in child.label
                or "\r" in child.label
            ):
                raise MemoryValidationError(
                    "split child labels must be nonblank trimmed single lines"
                )
            self._validate_label(child.label)
            if child.label in labels:
                raise MemoryValidationError("split child labels must be distinct")
            labels.add(child.label)

            if not child.body.strip():
                raise MemoryValidationError("split child bodies must be nonblank")
            self._validate_body(child.body)
            normalized_body = child.body.strip()
            if normalized_body in bodies:
                raise MemoryValidationError("split child bodies must be distinct")
            bodies.add(normalized_body)

            self._validate_split_keywords(child.keywords)

    @staticmethod
    def _validate_split_keywords(keywords: Sequence[str]) -> None:
        if not 2 <= len(keywords) <= 5:
            raise MemoryValidationError("split children require 2-5 keywords")
        seen: set[str] = set()
        for keyword in keywords:
            if not keyword or keyword != keyword.strip() or keyword != keyword.lower():
                raise MemoryValidationError(
                    "split child keywords must be trimmed nonblank lowercase terms"
                )
            if keyword in seen:
                raise MemoryValidationError("split child keywords must be distinct")
            seen.add(keyword)

    async def _dedup_matches(
        self,
        session: AsyncSession,
        *,
        principal_id: str,
        embedding: Sequence[float],
        statuses: Sequence[str] = ("active",),
        exclude_memory_ids: Sequence[UUID] = (),
    ) -> list[SimilarityMemoryCard]:
        unit = MemoryUnit.__table__
        cosine_score = (1.0 - unit.c.embedding.cosine_distance(list(embedding))).label("score")
        filters = [
            unit.c.principal_id == principal_id,
            unit.c.status.in_(tuple(statuses)),
            cosine_score >= self._dedup_sim,
        ]
        if exclude_memory_ids:
            filters.append(unit.c.id.not_in(tuple(exclude_memory_ids)))
        rows = (
            (
                await session.execute(
                    select(*unit.c, cosine_score)
                    .where(*filters)
                    .order_by(cosine_score.desc(), unit.c.id.asc())
                )
            )
            .mappings()
            .all()
        )
        return [_similarity_card_from_row(row) for row in rows]

    async def _insert_root(
        self,
        session: AsyncSession,
        command: CreateMemoryCommand,
        embedding: Sequence[float],
        *,
        status: str = "active",
        memory_id: UUID | None = None,
        run_id: str | None = None,
        origin_agent: str | None = None,
    ) -> Mapping[str, Any]:
        unit = MemoryUnit.__table__
        row = (
            (
                await session.execute(
                    insert(unit)
                    .values(
                        **({"id": memory_id} if memory_id is not None else {}),
                        principal_id=command.principal_id,
                        label=command.label,
                        body=command.body,
                        kind=command.kind,
                        keywords=list(command.keywords),
                        embedding=list(embedding),
                        embedding_model=self._embedding_provider.model,
                        project_key=command.project_key,
                        thread_origin=command.thread_origin,
                        origin_thread_id=command.origin_thread_id,
                        origin_path=command.origin_path,
                        origin_location=command.origin_location,
                        run_id=run_id,
                        origin_agent=origin_agent,
                        status=status,
                        pin=False,
                        revision=1,
                    )
                    .returning(*unit.c)
                )
            )
            .mappings()
            .one()
        )
        await session.execute(
            insert(MemoryRevision.__table__).values(
                rev_uid=mint_ulid(),
                parent_uid=command.parent_uid,
                memory_id=row["id"],
                revision=1,
                body=row["body"],
                label=row["label"],
                editor=command.editor,
                origin_machine_id=command.machine_id,
                reason=command.revision_reason,
            )
        )
        return row

    async def _staged_replay_row(
        self,
        session: AsyncSession,
        memory_id: UUID,
        command: CreateMemoryCommand,
    ) -> Mapping[str, Any] | None:
        row = (
            (
                await session.execute(
                    select(*MemoryUnit.__table__.c).where(MemoryUnit.id == memory_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        revision = (
            (
                await session.execute(
                    select(*MemoryRevision.__table__.c).where(
                        MemoryRevision.memory_id == memory_id,
                        MemoryRevision.revision == 1,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        expected = {
            "principal_id": command.principal_id,
            "label": command.label,
            "body": command.body,
            "kind": command.kind,
            "keywords": list(command.keywords),
            "project_key": command.project_key,
            "origin_thread_id": command.origin_thread_id,
            "origin_path": command.origin_path,
            "origin_location": command.origin_location,
            "status": "staged",
        }
        if any(row[key] != value for key, value in expected.items()) or revision is None:
            raise StagedMemoryConflictError("memory_id already names a different memory")
        revision_expected = {
            "editor": command.editor,
            "origin_machine_id": command.machine_id,
            "reason": command.revision_reason,
        }
        if any(revision[key] != value for key, value in revision_expected.items()):
            raise StagedMemoryConflictError("memory_id already names different staging provenance")
        return row


async def _find_active_label(
    session: AsyncSession,
    *,
    principal_id: str,
    label: str,
    exclude_memory_id: UUID | None = None,
) -> Mapping[str, Any] | None:
    unit = MemoryUnit.__table__
    filters = [
        unit.c.principal_id == principal_id,
        unit.c.label == label,
        unit.c.status == "active",
    ]
    if exclude_memory_id is not None:
        filters.append(unit.c.id != exclude_memory_id)
    return (
        (
            await session.execute(
                select(unit.c.id, unit.c.label).where(*filters).order_by(unit.c.id.asc()).limit(1)
            )
        )
        .mappings()
        .one_or_none()
    )


async def _patch_label_conflict(
    session: AsyncSession,
    command: PatchMemoryCommand,
    change_values: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    unit = MemoryUnit.__table__
    current = (
        (await session.execute(select(*unit.c).where(unit.c.id == command.memory_id)))
        .mappings()
        .one_or_none()
    )
    if current is None:
        return None
    resulting_status = change_values.get("status", current["status"])
    if resulting_status != "active":
        return None
    return await _find_active_label(
        session,
        principal_id=current["principal_id"],
        label=change_values.get("label", current["label"]),
        exclude_memory_id=command.memory_id,
    )


def contract_memory_from_snapshot(snapshot: MemoryUnitSnapshot) -> ContractMemoryUnit:
    return ContractMemoryUnit(
        memory_id=snapshot.id,
        principal_id=snapshot.principal_id,
        label=snapshot.label,
        body=snapshot.body,
        kind=snapshot.kind,
        keywords=list(snapshot.keywords),
        project_key=snapshot.project_key,
        thread_origin=snapshot.thread_origin,
        origin_thread_id=snapshot.origin_thread_id,
        origin_path=snapshot.origin_path,
        origin_location=snapshot.origin_location,
        pin=snapshot.pin,
        status=snapshot.status,
        revision=snapshot.revision,
        stats=snapshot.stats,
        bias=snapshot.bias,
        embedding_model=snapshot.embedding_model,
        created_at=snapshot.created_at,
        updated_at=snapshot.updated_at,
    )


def contract_memory_from_row(row: Mapping[str, Any]) -> ContractMemoryUnit:
    return contract_memory_from_snapshot(MemoryUnitSnapshot.from_row(row))


def _similarity_card_from_row(row: Mapping[str, Any]) -> SimilarityMemoryCard:
    return SimilarityMemoryCard(
        memory_id=row["id"],
        label=row["label"],
        body=row["body"],
        kind=row["kind"],
        pin=row["pin"],
        score=float(row["score"]),
        features=None,
        rank=None,
    )


def _provided(value: object) -> bool:
    return value is not UNSET and value is not None


def _require_staged_identity(row: Mapping[str, Any], *, run_id: str, origin_agent: str) -> None:
    if row["run_id"] != run_id or row["origin_agent"] != origin_agent:
        raise StagedMemoryConflictError("memory_id already names a different run attempt")


def _classify_dedup_score(
    score: float | None,
    *,
    dedup_sim: float,
    dedup_dup: float,
) -> Literal["distinct", "similar", "duplicate"]:
    if score is None or score < dedup_sim:
        return "distinct"
    if score < dedup_dup:
        return "similar"
    return "duplicate"


def _escape_ilike(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _integrity_constraint_name(error: IntegrityError) -> str | None:
    candidate: object | None = error.orig
    for _ in range(2):
        name = getattr(candidate, "constraint_name", None)
        if isinstance(name, str):
            return name
        candidate = getattr(candidate, "__cause__", None)
    return None


def _agent_from_editor(editor: str) -> str | None:
    prefix = "agent:"
    if not editor.startswith(prefix):
        return None
    agent = editor[len(prefix) :].strip()
    return agent or None


def _optional_uuid(value: str | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


__all__ = [
    "CreateMemoryCommand",
    "contract_memory_from_row",
    "contract_memory_from_snapshot",
    "DuplicateMemoryError",
    "EmptyPatchError",
    "InvalidListQueryError",
    "InvalidSearchQueryError",
    "LabelConflictError",
    "ListMemoriesQuery",
    "MemoryCreated",
    "MemoryNotFoundError",
    "MemoryService",
    "PreparedSplitChild",
    "MemoryServiceError",
    "MemorySplitCreated",
    "MemoryValidationError",
    "PatchMemoryCommand",
    "RevisionConflictError",
    "SearchMemoriesQuery",
    "SimilarMemories",
    "SplitMemoryChild",
    "SplitMemoryCommand",
    "UNSET",
    "UnsetType",
]
