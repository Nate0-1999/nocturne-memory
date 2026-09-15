"""Transactional M2H queue birth and deterministic verdict mechanics."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePath
from typing import Any
from uuid import UUID

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spine.contracts import SimilarityMemoryCard
from spine.db.memory import CasUpdate, MemoryUnitChanges, cas_update_memory_unit
from spine.db.models import (
    ApprovalDecision,
    ApprovalQueueItem,
    MemoryEdge,
    MemoryUnit,
)
from spine.ids import mint_ulid
from spine.memory.service import (
    CandidateCreated,
    CreateMemoryCommand,
    MemoryService,
    PreparedSplitChild,
    SplitMemoryChild,
    contract_memory_from_row,
)
from spine.queue.contracts import (
    BatchDecisionResponse,
    ExtractionRequest,
    ExtractionResponse,
    QueueCard,
    QueueDecisionRequest,
    QueueDecisionResponse,
    QueueResponse,
    SeedRequest,
    SeedResponse,
)


class QueueNotFoundError(LookupError):
    pass


class QueueConflictError(RuntimeError):
    pass


class QueueValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _ExistingDecision:
    decision_uid: str
    decision: str
    approval_mode: str
    actor_class: str


class QueueService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        memory_service: MemoryService,
    ) -> None:
        self._session_factory = session_factory
        self._memory_service = memory_service

    async def extract(self, request: ExtractionRequest) -> ExtractionResponse:
        cards: list[QueueCard] = []
        duplicates = 0
        for draft in request.candidates:
            created = await self._memory_service.create_candidate(
                CreateMemoryCommand(
                    principal_id=request.principal_id,
                    label=draft.label,
                    body=draft.body,
                    kind=draft.kind,
                    keywords=draft.keywords,
                    project_key=draft.project_key,
                    thread_origin=str(request.thread_id),
                    origin_thread_id=request.thread_id,
                    origin_location=request.origin_location,
                    editor=request.editor,
                    machine_id=request.machine_id,
                )
            )
            if created is None:
                duplicates += 1
                continue
            cards.append(await self._enqueue(request, draft.verdict, draft.target_ids, created))
        return ExtractionResponse(cards=cards, duplicate_count=duplicates)

    async def ingest_seed(self, request: SeedRequest) -> SeedResponse:
        self._validate_seed(request)
        existing = await self._seed_batch(request)
        if existing is not None:
            return SeedResponse(
                batch_uid=request.batch_uid,
                cards=existing,
                duplicate_count=len(request.candidates) - len(existing),
            )
        source = await self._memory_service.create_split_source(
            CreateMemoryCommand(
                principal_id=request.principal_id,
                label=f"Seed source · {request.source_name}"[:120],
                body=request.markdown,
                kind="project_note",
                keywords=("seed", "source"),
                thread_origin=f"seed:{request.batch_uid}",
                origin_path=request.source_name,
                editor=request.editor,
                machine_id=request.machine_id,
                revision_reason="seed_source_split",
            )
        )
        cards: list[QueueCard] = []
        duplicates = 0
        for draft in request.candidates:
            created = await self._memory_service.create_candidate(
                CreateMemoryCommand(
                    principal_id=request.principal_id,
                    label=draft.label,
                    body=draft.body,
                    kind=draft.kind,
                    keywords=draft.keywords,
                    project_key=draft.project_key,
                    origin_path=request.source_name,
                    editor=request.editor,
                    machine_id=request.machine_id,
                    parent_uid=source.revision_uid,
                    revision_reason="seed_split_child",
                )
            )
            if created is None:
                duplicates += 1
                continue
            cards.append(
                await self._enqueue_seed(request, draft.verdict, draft.target_ids, created)
            )
        await self._relate_siblings([card.candidate.memory_id for card in cards])
        return SeedResponse(
            batch_uid=request.batch_uid,
            cards=cards,
            duplicate_count=duplicates,
        )

    async def list_pending(
        self,
        principal_id: str,
        thread_id: UUID | None = None,
        birthplace: str | None = None,
    ) -> QueueResponse:
        queue = ApprovalQueueItem.__table__
        filters = [queue.c.principal_id == principal_id, queue.c.state == "pending"]
        if thread_id is not None:
            filters.append(queue.c.birthplace_thread_id == thread_id)
        if birthplace is not None:
            filters.append(queue.c.birthplace == birthplace)
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(*queue.c)
                        .where(*filters)
                        .order_by(queue.c.created_at, queue.c.item_uid)
                    )
                )
                .mappings()
                .all()
            )
            cards = [await self._card(session, row) for row in rows]
        return QueueResponse(cards=cards)

    async def list_batch(self, batch_uid: UUID, *, birthplace: str) -> list[QueueCard]:
        """Return one complete grouped consent batch, including decided cards."""

        queue = ApprovalQueueItem.__table__
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(*queue.c)
                        .where(
                            queue.c.batch_uid == batch_uid,
                            queue.c.birthplace == birthplace,
                        )
                        .order_by(queue.c.created_at, queue.c.item_uid)
                    )
                )
                .mappings()
                .all()
            )
            return [await self._card(session, row) for row in rows]

    async def enqueue_curator(
        self,
        *,
        run_uid: str,
        finding_uid: str,
        principal_id: str,
        machine_id: str,
        action: str,
        memory_ids: list[UUID],
        proposal: dict[str, Any],
    ) -> QueueCard | None:
        """Route one surgeon verdict through the ordinary corpus-born consent queue."""

        if not memory_ids:
            raise QueueValidationError("curator actions require implicated memories")
        source = await self._memory_row(memory_ids[0], principal_id=principal_id)
        candidate: CandidateCreated | None = None
        target_ids: list[UUID] = []
        if action in {"merge", "supersede"}:
            candidate = await self._memory_service.create_curator_candidate(
                CreateMemoryCommand(
                    principal_id=principal_id,
                    label=_proposal_text(proposal, "label"),
                    body=_proposal_text(proposal, "body"),
                    kind=source["kind"],
                    keywords=_proposal_keywords(proposal),
                    project_key=source["project_key"],
                    thread_origin=source["thread_origin"],
                    origin_thread_id=source["origin_thread_id"],
                    origin_path=source["origin_path"],
                    editor="maintenance",
                    machine_id=machine_id,
                    revision_reason=f"curation/{finding_uid}/proposal",
                ),
                exclude_memory_ids=memory_ids,
            )
            if candidate is None:
                return None
            candidate_row = await self._memory_row(
                candidate.memory.memory_id, principal_id=principal_id
            )
            target_ids = list(memory_ids)
            neighbors = list(candidate.neighbors)
        else:
            candidate_row = source
            neighbors = []
            if action == "contradict":
                target_ids = list(memory_ids[1:])

        row_values = {
            "item_uid": mint_ulid(),
            "candidate_memory_id": candidate_row["id"],
            "principal_id": principal_id,
            "birthplace": "curator",
            "birthplace_thread_id": None,
            "batch_uid": None,
            "source_name": None,
            "source_sha256": None,
            "birthplace_run_id": None,
            "birthplace_origin_agent": None,
            "candidate_revision": candidate_row["revision"],
            "curator_run_uid": run_uid,
            "curator_finding_uid": finding_uid,
            "proposal_payload": proposal,
            "verdict": action,
            "neighbor_ids": [str(item.memory_id) for item in neighbors],
            "target_ids": [str(item) for item in target_ids],
            "state": "pending",
        }
        async with self._session_factory() as session:
            async with session.begin():
                row = (
                    (
                        await session.execute(
                            insert(ApprovalQueueItem.__table__)
                            .values(**row_values)
                            .returning(*ApprovalQueueItem.__table__.c)
                        )
                    )
                    .mappings()
                    .one()
                )
                return await self._card(session, row, neighbors=neighbors)

    async def decide(self, item_uid: str, request: QueueDecisionRequest) -> QueueDecisionResponse:
        queue = ApprovalQueueItem.__table__
        prepared_split: tuple[PreparedSplitChild, ...] | None = None
        async with self._session_factory() as session:
            preflight = (
                (
                    await session.execute(select(*queue.c).where(queue.c.item_uid == item_uid))
                )
                .mappings()
                .one_or_none()
            )
        if preflight is not None and preflight["birthplace"] == "curator" and preflight[
            "verdict"
        ] == "split":
            payload = preflight["proposal_payload"]
            children = _proposal_children(payload)
            prepared_split = await self._memory_service.prepare_curator_split(
                principal_id=preflight["principal_id"],
                machine_id=request.machine_id,
                children=children,
            )
        async with self._session_factory() as session:
            async with session.begin():
                row = (
                    (
                        await session.execute(
                            select(*queue.c).where(queue.c.item_uid == item_uid).with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    raise QueueNotFoundError(item_uid)
                return await self._decide_row(
                    session, row, request, prepared_split=prepared_split
                )

    async def decide_batch(
        self, batch_uid: UUID, request: QueueDecisionRequest
    ) -> BatchDecisionResponse:
        if request.approval_mode != "explicit" or request.actor_class != "human":
            raise QueueValidationError("queue batches require an explicit human decision")
        queue = ApprovalQueueItem.__table__
        async with self._session_factory() as session:
            async with session.begin():
                rows = (
                    (
                        await session.execute(
                            select(*queue.c)
                            .where(
                                queue.c.batch_uid == batch_uid,
                                queue.c.birthplace.in_(("seed", "symphony")),
                            )
                            .order_by(queue.c.created_at, queue.c.item_uid)
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .all()
                )
                if not rows:
                    raise QueueNotFoundError(str(batch_uid))
                responses = [await self._decide_row(session, row, request) for row in rows]
                return BatchDecisionResponse(
                    batch_uid=batch_uid,
                    decision=request.decision,
                    cards=[response.card for response in responses],
                )

    async def _decide_row(
        self,
        session: AsyncSession,
        row: Any,
        request: QueueDecisionRequest,
        *,
        prepared_split: tuple[PreparedSplitChild, ...] | None = None,
    ) -> QueueDecisionResponse:
        decisions = ApprovalDecision.__table__
        queue = ApprovalQueueItem.__table__
        prior_row = (
            (
                await session.execute(
                    select(*decisions.c).where(decisions.c.item_uid == row["item_uid"])
                )
            )
            .mappings()
            .one_or_none()
        )
        if prior_row is not None:
            prior = _ExistingDecision(
                prior_row["decision_uid"],
                prior_row["decision"],
                prior_row["approval_mode"],
                prior_row["actor_class"],
            )
            if (prior.decision, prior.approval_mode, prior.actor_class) != (
                request.decision,
                request.approval_mode,
                request.actor_class,
            ):
                raise QueueConflictError("queue item already has a different decision")
            return QueueDecisionResponse(
                card=await self._card(session, row),
                decision=prior.decision,
                approval_mode=prior.approval_mode,
                actor_class=prior.actor_class,
                decision_uid=prior.decision_uid,
            )
        if row["verdict"] == "contradict" and request.approval_mode == "passive":
            raise QueueValidationError("contradictions require explicit approval")
        candidate = await self._locked_memory(session, row["candidate_memory_id"])
        if row["birthplace"] == "curator":
            return await self._decide_curator_row(
                session,
                row,
                candidate,
                request,
                prepared_split=prepared_split,
            )
        expected = "approved" if request.decision == "approve" else "rejected"
        if candidate["status"] != "candidate":
            raise QueueConflictError("queue candidate is no longer pending")
        await cas_update_memory_unit(
            session,
            CasUpdate(
                memory_id=candidate["id"],
                expected_revision=candidate["revision"],
                rev_uid=mint_ulid(),
                editor="human" if request.actor_class == "human" else "passive",
                origin_machine_id=request.machine_id,
                reason="approved" if request.decision == "approve" else "rejected",
                changes=MemoryUnitChanges(
                    status="active" if request.decision == "approve" else "tombstoned"
                ),
            ),
        )
        if request.decision == "approve":
            await self._enact_targets(session, row, candidate, request.machine_id)
        decision_uid = mint_ulid()
        await session.execute(
            insert(decisions).values(
                decision_uid=decision_uid,
                item_uid=row["item_uid"],
                decision=request.decision,
                approval_mode=request.approval_mode,
                actor_class=request.actor_class,
            )
        )
        updated = (
            (
                await session.execute(
                    update(queue)
                    .where(queue.c.item_uid == row["item_uid"])
                    .values(state=expected, decided_at=func.now())
                    .returning(*queue.c)
                )
            )
            .mappings()
            .one()
        )
        return QueueDecisionResponse(
            card=await self._card(session, updated),
            decision=request.decision,
            approval_mode=request.approval_mode,
            actor_class=request.actor_class,
            decision_uid=decision_uid,
        )

    async def _decide_curator_row(
        self,
        session: AsyncSession,
        row: Any,
        candidate: Any,
        request: QueueDecisionRequest,
        *,
        prepared_split: tuple[PreparedSplitChild, ...] | None,
    ) -> QueueDecisionResponse:
        if request.approval_mode != "explicit" or request.actor_class != "human":
            raise QueueValidationError("curator verdicts require an explicit human decision")
        if candidate["revision"] != row["candidate_revision"]:
            raise QueueConflictError("curator target changed after diagnosis; run curators again")

        action = row["verdict"]
        if request.decision == "deny":
            if candidate["status"] == "candidate":
                await self._curator_cas(
                    session,
                    candidate,
                    request.machine_id,
                    row,
                    suffix="rejected",
                    changes=MemoryUnitChanges(status="tombstoned"),
                )
        elif action in {"merge", "supersede"}:
            if candidate["status"] != "candidate":
                raise QueueConflictError("curator replacement is no longer pending")
            await self._curator_cas(
                session,
                candidate,
                request.machine_id,
                row,
                suffix="activate",
                changes=MemoryUnitChanges(status="active"),
            )
            await self._enact_targets(session, row, candidate, request.machine_id)
        elif action == "contradict":
            if candidate["status"] != "active":
                raise QueueConflictError("curator contradiction source is no longer active")
            await self._enact_targets(session, row, candidate, request.machine_id)
        elif action == "retire":
            await self._curator_cas(
                session,
                candidate,
                request.machine_id,
                row,
                suffix="retire",
                changes=MemoryUnitChanges(status="tombstoned"),
            )
        elif action == "keyword_repair":
            await self._curator_cas(
                session,
                candidate,
                request.machine_id,
                row,
                suffix="keyword-repair",
                changes=MemoryUnitChanges(keywords=_proposal_keywords(row["proposal_payload"])),
            )
        elif action == "split":
            if prepared_split is None:
                raise QueueValidationError("curator split children were not prepared")
            await self._memory_service.enact_curator_split(
                session,
                source=candidate,
                children=prepared_split,
                machine_id=request.machine_id,
                finding_uid=row["curator_finding_uid"],
            )
        else:  # pragma: no cover - database check owns the closed action set
            raise QueueValidationError("unknown curator action")

        decision_uid = mint_ulid()
        await session.execute(
            insert(ApprovalDecision.__table__).values(
                decision_uid=decision_uid,
                item_uid=row["item_uid"],
                decision=request.decision,
                approval_mode=request.approval_mode,
                actor_class=request.actor_class,
            )
        )
        updated = (
            (
                await session.execute(
                    update(ApprovalQueueItem.__table__)
                    .where(ApprovalQueueItem.item_uid == row["item_uid"])
                    .values(
                        state="approved" if request.decision == "approve" else "rejected",
                        decided_at=func.now(),
                    )
                    .returning(*ApprovalQueueItem.__table__.c)
                )
            )
            .mappings()
            .one()
        )
        return QueueDecisionResponse(
            card=await self._card(session, updated),
            decision=request.decision,
            approval_mode=request.approval_mode,
            actor_class=request.actor_class,
            decision_uid=decision_uid,
        )

    @staticmethod
    async def _curator_cas(
        session: AsyncSession,
        candidate: Any,
        machine_id: str,
        row: Any,
        *,
        suffix: str,
        changes: MemoryUnitChanges,
    ) -> None:
        await cas_update_memory_unit(
            session,
            CasUpdate(
                memory_id=candidate["id"],
                expected_revision=candidate["revision"],
                rev_uid=mint_ulid(),
                editor="maintenance",
                origin_machine_id=machine_id,
                reason=f"curation/{row['curator_finding_uid']}/{suffix}",
                changes=changes,
            ),
        )

    async def _enqueue(
        self,
        request: ExtractionRequest,
        verdict: str,
        target_ids: list[UUID],
        created: CandidateCreated,
    ) -> QueueCard:
        active_neighbors = [
            neighbor
            for neighbor in created.neighbors
            if await self._is_active(request.principal_id, neighbor.memory_id)
        ]
        neighbor_ids = {neighbor.memory_id for neighbor in active_neighbors}
        if any(target not in neighbor_ids for target in target_ids):
            await self._discard_candidate(created.memory.memory_id, request.machine_id)
            raise QueueValidationError("verdict targets must be active machine-fetched neighbors")
        item_uid = mint_ulid()
        async with self._session_factory() as session:
            async with session.begin():
                row = (
                    (
                        await session.execute(
                            insert(ApprovalQueueItem.__table__)
                            .values(
                                item_uid=item_uid,
                                candidate_memory_id=created.memory.memory_id,
                                principal_id=request.principal_id,
                                birthplace="thread",
                                birthplace_thread_id=request.thread_id,
                                verdict=verdict,
                                neighbor_ids=[str(item.memory_id) for item in active_neighbors],
                                target_ids=[str(item) for item in target_ids],
                                state="pending",
                            )
                            .returning(*ApprovalQueueItem.__table__.c)
                        )
                    )
                    .mappings()
                    .one()
                )
                return await self._card(session, row, neighbors=active_neighbors)

    async def _enqueue_seed(
        self,
        request: SeedRequest,
        verdict: str,
        target_ids: list[UUID],
        created: CandidateCreated,
    ) -> QueueCard:
        active_neighbors = [
            neighbor
            for neighbor in created.neighbors
            if await self._is_active(request.principal_id, neighbor.memory_id)
        ]
        neighbor_ids = {neighbor.memory_id for neighbor in active_neighbors}
        if any(target not in neighbor_ids for target in target_ids):
            await self._discard_candidate(created.memory.memory_id, request.machine_id)
            raise QueueValidationError("verdict targets must be active machine-fetched neighbors")
        async with self._session_factory() as session:
            async with session.begin():
                row = (
                    (
                        await session.execute(
                            insert(ApprovalQueueItem.__table__)
                            .values(
                                item_uid=mint_ulid(),
                                candidate_memory_id=created.memory.memory_id,
                                principal_id=request.principal_id,
                                birthplace="seed",
                                birthplace_thread_id=None,
                                batch_uid=request.batch_uid,
                                source_name=request.source_name,
                                source_sha256=request.source_sha256,
                                verdict=verdict,
                                neighbor_ids=[str(item.memory_id) for item in active_neighbors],
                                target_ids=[str(item) for item in target_ids],
                                state="pending",
                            )
                            .returning(*ApprovalQueueItem.__table__.c)
                        )
                    )
                    .mappings()
                    .one()
                )
                return await self._card(session, row, neighbors=active_neighbors)

    async def _seed_batch(self, request: SeedRequest) -> list[QueueCard] | None:
        queue = ApprovalQueueItem.__table__
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(*queue.c)
                        .where(queue.c.batch_uid == request.batch_uid)
                        .order_by(queue.c.created_at, queue.c.item_uid)
                    )
                )
                .mappings()
                .all()
            )
            if rows:
                first = rows[0]
                if (
                    first["principal_id"] != request.principal_id
                    or first["source_name"] != request.source_name
                    or first["source_sha256"] != request.source_sha256
                ):
                    raise QueueConflictError("seed batch UID already names a different document")
                return [await self._card(session, row) for row in rows]
            rejected_source = await session.scalar(
                select(queue.c.item_uid)
                .where(
                    queue.c.principal_id == request.principal_id,
                    queue.c.birthplace == "seed",
                    queue.c.source_sha256 == request.source_sha256,
                    queue.c.state == "rejected",
                )
                .limit(1)
            )
            if rejected_source is not None:
                return []
            source = (
                (
                    await session.execute(
                        select(*MemoryUnit.__table__.c).where(
                            MemoryUnit.principal_id == request.principal_id,
                            MemoryUnit.thread_origin == f"seed:{request.batch_uid}",
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if source is None:
                return None
            if (
                source["origin_path"] != request.source_name
                or sha256(source["body"].encode("utf-8")).hexdigest() != request.source_sha256
            ):
                raise QueueConflictError("seed batch UID already names a different document")
            return []

    async def _relate_siblings(self, memory_ids: list[UUID]) -> None:
        if len(memory_ids) < 2:
            return
        async with self._session_factory() as session:
            async with session.begin():
                for left in memory_ids:
                    for right in memory_ids:
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

    @staticmethod
    def _validate_seed(request: SeedRequest) -> None:
        valid_basename = PurePath(request.source_name).name == request.source_name
        valid_extension = request.source_name.lower().endswith((".md", ".markdown"))
        if not valid_basename or not valid_extension:
            raise QueueValidationError("source_name must be a Markdown basename")
        if not request.markdown.strip():
            raise QueueValidationError("seed markdown must not be blank")
        if len(request.markdown.encode("utf-8")) > 24 * 1024:
            raise QueueValidationError("seed markdown exceeds the 24 KiB limit")
        if sha256(request.markdown.encode("utf-8")).hexdigest() != request.source_sha256:
            raise QueueValidationError("seed markdown digest does not match source_sha256")

    async def _is_active(self, principal_id: str, memory_id: UUID) -> bool:
        async with self._session_factory() as session:
            return bool(
                await session.scalar(
                    select(func.count())
                    .select_from(MemoryUnit)
                    .where(
                        MemoryUnit.id == memory_id,
                        MemoryUnit.principal_id == principal_id,
                        MemoryUnit.status == "active",
                    )
                )
            )

    async def _card(
        self,
        session: AsyncSession,
        row: Any,
        *,
        neighbors: list[SimilarityMemoryCard] | None = None,
    ) -> QueueCard:
        candidate = (
            (
                await session.execute(
                    select(*MemoryUnit.__table__.c).where(
                        MemoryUnit.id == row["candidate_memory_id"]
                    )
                )
            )
            .mappings()
            .one()
        )
        if neighbors is None:
            unit = MemoryUnit.__table__
            cosine_score = (
                1.0 - unit.c.embedding.cosine_distance(list(candidate["embedding"]))
            ).label("score")
            neighbor_rows = (
                (
                    await session.execute(
                        select(*unit.c, cosine_score).where(
                            unit.c.id.in_([UUID(value) for value in row["neighbor_ids"]])
                        )
                    )
                )
                .mappings()
                .all()
            )
            by_id = {item["id"]: item for item in neighbor_rows}
            neighbors = [
                self._neighbor_card(by_id[UUID(value)])
                for value in row["neighbor_ids"]
                if UUID(value) in by_id
            ]
        return QueueCard(
            item_uid=row["item_uid"],
            candidate=contract_memory_from_row(candidate),
            birthplace=row["birthplace"],
            birthplace_thread_id=row["birthplace_thread_id"],
            batch_uid=row["batch_uid"],
            source_name=row["source_name"],
            source_sha256=row["source_sha256"],
            birthplace_run_id=row["birthplace_run_id"],
            birthplace_origin_agent=row["birthplace_origin_agent"],
            judged_context=row["judged_context"],
            candidate_revision=row["candidate_revision"],
            curator_run_uid=row["curator_run_uid"],
            curator_finding_uid=row["curator_finding_uid"],
            proposal_payload=row["proposal_payload"],
            verdict=row["verdict"],
            neighbors=neighbors,
            target_ids=[UUID(value) for value in row["target_ids"]],
            state=row["state"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _neighbor_card(row: Any) -> SimilarityMemoryCard:
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

    async def _discard_candidate(self, memory_id: UUID, machine_id: str) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                candidate = await self._locked_memory(session, memory_id)
                await cas_update_memory_unit(
                    session,
                    CasUpdate(
                        memory_id=memory_id,
                        expected_revision=candidate["revision"],
                        rev_uid=mint_ulid(),
                        editor="extraction",
                        origin_machine_id=machine_id,
                        reason="invalid_queue_target",
                        changes=MemoryUnitChanges(status="tombstoned"),
                    ),
                )

    async def _memory_row(self, memory_id: UUID, *, principal_id: str) -> Any:
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        select(*MemoryUnit.__table__.c).where(
                            MemoryUnit.id == memory_id,
                            MemoryUnit.principal_id == principal_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise QueueValidationError(f"curator memory {memory_id} is missing")
        return row

    @staticmethod
    async def _locked_memory(session: AsyncSession, memory_id: UUID) -> Any:
        row = (
            (
                await session.execute(
                    select(*MemoryUnit.__table__.c)
                    .where(MemoryUnit.id == memory_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise QueueConflictError(f"memory {memory_id} is missing")
        return row

    async def _enact_targets(
        self, session: AsyncSession, row: Any, candidate: Any, machine_id: str
    ) -> None:
        edge_type = {
            "merge": "merged_from",
            "supersede": "supersedes",
            "contradict": "contradicts",
        }.get(row["verdict"])
        if edge_type is None:
            return
        for raw_target in row["target_ids"]:
            target = await self._locked_memory(session, UUID(raw_target))
            if target["principal_id"] != candidate["principal_id"] or target["status"] != "active":
                raise QueueConflictError(
                    "verdict target is no longer an active same-principal unit"
                )
            if row["verdict"] in {"merge", "supersede"}:
                reason = row["verdict"]
                if row["birthplace"] == "curator":
                    reason = f"curation/{row['curator_finding_uid']}/{row['verdict']}"
                await cas_update_memory_unit(
                    session,
                    CasUpdate(
                        memory_id=target["id"],
                        expected_revision=target["revision"],
                        rev_uid=mint_ulid(),
                        editor="maintenance",
                        origin_machine_id=machine_id,
                        reason=reason,
                        changes=MemoryUnitChanges(status="tombstoned"),
                    ),
                )
            await session.execute(
                insert(MemoryEdge.__table__).values(
                    edge_uid=mint_ulid(),
                    from_memory_id=candidate["id"],
                    to_memory_id=target["id"],
                    edge_type=edge_type,
                )
            )


def _proposal_text(proposal: dict[str, Any], key: str) -> str:
    value = proposal.get(key)
    if not isinstance(value, str) or not value.strip():
        raise QueueValidationError(f"curator {key} must be nonblank text")
    return value


def _proposal_keywords(proposal: dict[str, Any]) -> list[str]:
    value = proposal.get("keywords")
    if not isinstance(value, list) or not 2 <= len(value) <= 5:
        raise QueueValidationError("curator keywords must contain 2-5 values")
    if any(
        not isinstance(item, str)
        or not item
        or item != item.strip()
        or item != item.lower()
        for item in value
    ) or len(set(value)) != len(value):
        raise QueueValidationError("curator keywords must be distinct lowercase terms")
    return value


def _proposal_children(proposal: dict[str, Any]) -> list[SplitMemoryChild]:
    raw = proposal.get("children")
    if not isinstance(raw, list):
        raise QueueValidationError("curator split requires children")
    children: list[SplitMemoryChild] = []
    for item in raw:
        if not isinstance(item, dict):
            raise QueueValidationError("curator split child must be an object")
        children.append(
            SplitMemoryChild(
                label=_proposal_text(item, "label"),
                body=_proposal_text(item, "body"),
                keywords=_proposal_keywords(item),
            )
        )
    return children
