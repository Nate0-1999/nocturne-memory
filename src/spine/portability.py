"""Principal-scoped memory archives with intact revision and relationship lineage."""

import json
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spine.contracts import ContractModel
from spine.db.models import (
    ApprovalDecision,
    ApprovalQueueItem,
    MemoryEdge,
    MemoryRevision,
    MemoryUnit,
)
from spine.problems import ProblemJSONResponse, problem_response

router = APIRouter(tags=["portability"])
_TABLES = (MemoryUnit, MemoryRevision, MemoryEdge, ApprovalQueueItem, ApprovalDecision)
_SCOPES = (
    "t.principal_id = :principal",
    "t.memory_id IN (SELECT id FROM memory_unit WHERE principal_id = :principal)",
    "t.from_memory_id IN (SELECT id FROM memory_unit WHERE principal_id = :principal) "
    "AND t.to_memory_id IN (SELECT id FROM memory_unit WHERE principal_id = :principal)",
    "t.principal_id = :principal",
    "t.item_uid IN (SELECT item_uid FROM approval_queue_item WHERE principal_id = :principal)",
)


class MemoryArchive(ContractModel):
    format: Literal["nocturne-memories-v1"] = "nocturne-memories-v1"
    principal_id: str = Field(min_length=1)
    memories: list[dict[str, Any]]
    revisions: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    queue: list[dict[str, Any]]
    decisions: list[dict[str, Any]]


class PortabilityService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def export(self, principal_id: str) -> MemoryArchive:
        async with self._sessions() as session, session.begin():
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            groups = []
            for model, scope in zip(_TABLES, _SCOPES, strict=True):
                # Identifiers and predicates are fixed here, never supplied by the archive.
                rows = await session.scalars(
                    text(f"SELECT row_to_json(t) FROM {model.__tablename__} t WHERE {scope}"),
                    {"principal": principal_id},
                )
                groups.append(list(rows))
        return MemoryArchive(
            principal_id=principal_id,
            **dict(
                zip(
                    ("memories", "revisions", "edges", "queue", "decisions"),
                    groups,
                    strict=True,
                )
            ),
        )

    async def import_archive(self, principal_id: str, archive: MemoryArchive) -> dict[str, int]:
        if principal_id != archive.principal_id:
            raise ValueError("The archive belongs to a different principal.")
        memory_ids = {row.get("id") for row in archive.memories}
        revision_ids = {row.get("rev_uid") for row in archive.revisions}
        queue_ids = {row.get("item_uid") for row in archive.queue}
        if any(
            row.get("principal_id") != principal_id for row in (*archive.memories, *archive.queue)
        ):
            raise ValueError("The archive contains a different principal.")
        if any(
            row.get("memory_id") not in memory_ids
            or (row.get("parent_uid") is not None and row["parent_uid"] not in revision_ids)
            for row in archive.revisions
        ):
            raise ValueError("The archive has incomplete revision lineage.")
        if any(
            row.get("from_memory_id") not in memory_ids or row.get("to_memory_id") not in memory_ids
            for row in archive.edges
        ):
            raise ValueError("The archive has incomplete memory relationships.")
        if any(row.get("candidate_memory_id") not in memory_ids for row in archive.queue) or any(
            row.get("item_uid") not in queue_ids for row in archive.decisions
        ):
            raise ValueError("The archive has incomplete review lineage.")
        groups = (
            archive.memories,
            archive.revisions,
            archive.edges,
            archive.queue,
            archive.decisions,
        )
        async with self._sessions() as session, session.begin():
            for model, rows in zip(_TABLES, groups, strict=True):
                columns = ", ".join(
                    column.name for column in model.__table__.columns if column.name != "search_tsv"
                )
                await session.execute(
                    text(
                        f"INSERT INTO {model.__tablename__} ({columns}) "
                        f"SELECT {columns} FROM json_populate_recordset("
                        f"NULL::{model.__tablename__}, "
                        "CAST(:rows AS json))"
                    ),
                    {"rows": json.dumps(rows)},
                )
        return {
            "memories": len(archive.memories),
            "revisions": len(archive.revisions),
            "edges": len(archive.edges),
        }


@router.get("/v1/memories/export", response_model=MemoryArchive)
async def export_memories(principal_id: str, request: Request) -> MemoryArchive:
    return await request.app.state.portability_service.export(principal_id)


@router.post("/v1/memories/import", response_model=dict[str, int])
async def import_memories(
    principal_id: str,
    archive: MemoryArchive,
    request: Request,
) -> dict[str, int] | ProblemJSONResponse:
    try:
        return await request.app.state.portability_service.import_archive(principal_id, archive)
    except (ValueError, IntegrityError, DataError):
        return problem_response(
            status=409,
            title="Archive conflict",
            detail=(
                "The archive has conflicting IDs, incomplete lineage, or a different principal. "
                "Nothing was imported. Use a fresh Palace with the archive's principal."
            ),
            instance=request.url.path,
            endpoint="POST /v1/memories/import",
        )
