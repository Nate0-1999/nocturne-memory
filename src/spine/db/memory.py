"""Transactional C.2 writes for memory heads and append-only revisions."""

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import SessionTransactionOrigin

from spine.contracts import MemoryKind, MemoryStatus
from spine.db.models import MemoryRevision, MemoryUnit


class _Unset:
    __slots__ = ()


_UNSET = _Unset()
_ULID_PATTERN = re.compile(r"[0-7][0-9A-HJKMNP-TV-Z]{25}\Z")


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryUnitChanges:
    """Allowlisted mutable C.2 head fields; unset values are not written."""

    label: str | _Unset = _UNSET
    body: str | _Unset = _UNSET
    kind: MemoryKind | _Unset = _UNSET
    keywords: Sequence[str] | _Unset = _UNSET
    embedding: Sequence[float] | _Unset = _UNSET
    embedding_model: str | _Unset = _UNSET
    project_key: str | None | _Unset = _UNSET
    thread_origin: str | None | _Unset = _UNSET
    origin_thread_id: UUID | None | _Unset = _UNSET
    origin_path: str | None | _Unset = _UNSET
    origin_location: str | None | _Unset = _UNSET
    pin: bool | _Unset = _UNSET
    status: MemoryStatus | _Unset = _UNSET
    stats: Mapping[str, Any] | _Unset = _UNSET
    bias: float | _Unset = _UNSET

    def as_values(self) -> dict[str, Any]:
        """Copy explicitly supplied values into SQL-safe mutable containers."""

        values: dict[str, Any] = {}
        for name in (
            "label",
            "body",
            "kind",
            "keywords",
            "embedding",
            "embedding_model",
            "project_key",
            "thread_origin",
            "origin_thread_id",
            "origin_path",
            "origin_location",
            "pin",
            "status",
            "stats",
            "bias",
        ):
            value = getattr(self, name)
            if value is _UNSET:
                continue
            if name in {"keywords", "embedding"}:
                value = list(value)
            elif name == "stats":
                value = deepcopy(dict(value))
            values[name] = value
        return values


@dataclass(frozen=True, slots=True, kw_only=True)
class CasUpdate:
    """One caller-identified cloud-head update and its revision attribution."""

    memory_id: UUID
    expected_revision: int
    rev_uid: str
    editor: str
    origin_machine_id: str
    changes: MemoryUnitChanges
    reason: str = ""

    def __post_init__(self) -> None:
        if _ULID_PATTERN.fullmatch(self.rev_uid) is None:
            raise ValueError("rev_uid must be a canonical 26-character ULID")


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryUnitSnapshot:
    """Detached copy of a C.2 memory head, safe across commit or rollback."""

    id: UUID
    principal_id: str
    label: str
    body: str
    kind: str
    keywords: tuple[str, ...]
    embedding: tuple[float, ...]
    embedding_model: str
    project_key: str | None
    thread_origin: str | None
    origin_thread_id: UUID | None
    origin_path: str | None
    origin_location: str | None
    origin_locations: tuple[str, ...]
    pin: bool
    status: str
    revision: int
    stats: dict[str, Any]
    bias: float
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "MemoryUnitSnapshot":
        """Detach a SQLAlchemy row mapping from its transaction and identity map."""

        return cls(
            id=row["id"],
            principal_id=row["principal_id"],
            label=row["label"],
            body=row["body"],
            kind=row["kind"],
            keywords=tuple(row["keywords"]),
            embedding=tuple(float(value) for value in row["embedding"]),
            embedding_model=row["embedding_model"],
            project_key=row["project_key"],
            thread_origin=row["thread_origin"],
            origin_thread_id=row["origin_thread_id"],
            origin_path=row["origin_path"],
            origin_location=row["origin_location"],
            origin_locations=tuple(row["origin_locations"]),
            pin=row["pin"],
            status=row["status"],
            revision=row["revision"],
            stats=deepcopy(row["stats"]),
            bias=row["bias"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class MemoryCasConflictError(RuntimeError):
    """A stale cloud-head write; S2 maps this current snapshot to HTTP 409."""

    status_code: ClassVar[int] = 409

    def __init__(self, current: MemoryUnitSnapshot) -> None:
        self.current = current
        super().__init__(
            f"memory {current.id} is at revision {current.revision}; CAS write was stale"
        )


class MemoryUnitNotFoundError(LookupError):
    """The requested memory head does not exist."""

    def __init__(self, memory_id: UUID) -> None:
        self.memory_id = memory_id
        super().__init__(f"memory {memory_id} does not exist")


class MemoryLineageError(RuntimeError):
    """The expected cloud head does not have exactly one parent revision row."""

    def __init__(self, memory_id: UUID, expected_revision: int, matches: int) -> None:
        self.memory_id = memory_id
        self.expected_revision = expected_revision
        self.matches = matches
        super().__init__(
            f"memory {memory_id} revision {expected_revision} has {matches} lineage rows"
        )


async def cas_update_memory_unit(
    session: AsyncSession,
    command: CasUpdate,
) -> MemoryUnitSnapshot:
    """CAS-update a head and append its resulting revision in the caller's transaction.

    The caller must own an explicit transaction and write a given memory at most once
    in it. No commit occurs here. An internal savepoint keeps even a caught lineage or
    revision error from splitting the head and its history.
    """

    transaction = session.sync_session.get_transaction()
    if transaction is None or transaction.origin is not SessionTransactionOrigin.BEGIN:
        raise RuntimeError("cas_update_memory_unit requires an explicit caller transaction")

    async with session.begin_nested():
        return await _cas_update_memory_unit(session, command)


async def _cas_update_memory_unit(
    session: AsyncSession,
    command: CasUpdate,
) -> MemoryUnitSnapshot:
    """Execute one complete head-and-history write inside a savepoint."""

    values = command.changes.as_values()
    if not values:
        raise ValueError("a CAS update requires at least one changed field")

    unit = MemoryUnit.__table__
    values.update(
        revision=unit.c.revision + 1,
        updated_at=func.now(),
    )
    updated_row = (
        (
            await session.execute(
                update(unit)
                .where(
                    unit.c.id == command.memory_id,
                    unit.c.revision == command.expected_revision,
                )
                .values(**values)
                .returning(*unit.c)
            )
        )
        .mappings()
        .one_or_none()
    )

    if updated_row is None:
        current_row = (
            (
                await session.execute(
                    select(*unit.c).where(unit.c.id == command.memory_id).with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if current_row is None:
            raise MemoryUnitNotFoundError(command.memory_id)
        raise MemoryCasConflictError(MemoryUnitSnapshot.from_row(current_row))

    revision = MemoryRevision.__table__
    parent_uids = (
        await session.scalars(
            select(revision.c.rev_uid).where(
                revision.c.memory_id == command.memory_id,
                revision.c.revision == command.expected_revision,
            )
        )
    ).all()
    if len(parent_uids) != 1:
        raise MemoryLineageError(command.memory_id, command.expected_revision, len(parent_uids))

    await session.execute(
        insert(revision).values(
            rev_uid=command.rev_uid,
            parent_uid=parent_uids[0],
            memory_id=command.memory_id,
            revision=updated_row["revision"],
            body=updated_row["body"],
            label=updated_row["label"],
            editor=command.editor,
            origin_machine_id=command.origin_machine_id,
            reason=command.reason,
        )
    )

    return MemoryUnitSnapshot.from_row(updated_row)


async def tombstone_memory_unit(
    session: AsyncSession,
    *,
    memory_id: UUID,
    expected_revision: int,
    rev_uid: str,
    editor: str,
    origin_machine_id: str,
    reason: str = "",
) -> MemoryUnitSnapshot:
    """Revision-write a tombstone through the same CAS path; never delete the row."""

    return await cas_update_memory_unit(
        session,
        CasUpdate(
            memory_id=memory_id,
            expected_revision=expected_revision,
            rev_uid=rev_uid,
            editor=editor,
            origin_machine_id=origin_machine_id,
            reason=reason,
            changes=MemoryUnitChanges(status="tombstoned"),
        ),
    )
