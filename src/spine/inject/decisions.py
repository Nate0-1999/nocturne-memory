"""Transactional commit and feedback state transitions for one injection."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spine.contracts import CommitResponse, FeedbackResponse
from spine.db.memory import CasUpdate, MemoryUnitChanges, MemoryUnitSnapshot, cas_update_memory_unit
from spine.db.models import InjectionEvent, MemoryUnit, ScorerConfig
from spine.ids import mint_ulid
from spine.inject.renderer import render_final_block
from spine.memory.service import contract_memory_from_snapshot

RemovalReason = Literal["not_relevant", "wrong", "never"]
FeedbackSignal = Literal["mid_thread_removed", "mid_thread_added", "cited"]

_POSITIVE_OUTCOMES = frozenset({"kept", "added_back", "cited"})
_FEEDBACK_OUTCOMES = frozenset({"cited", "mid_thread_removed", "mid_thread_added"})


@dataclass(frozen=True, slots=True, kw_only=True)
class RemovedDecision:
    memory_id: UUID
    reason: RemovalReason


@dataclass(frozen=True, slots=True, kw_only=True)
class CommitCommand:
    injection_id: UUID
    removed: Sequence[RemovedDecision]
    added_back: Sequence[UUID]


@dataclass(frozen=True, slots=True, kw_only=True)
class FeedbackCommand:
    injection_id: UUID
    memory_id: UUID
    signal: FeedbackSignal


class DecisionServiceError(RuntimeError):
    """Base class for expected commit and feedback failures."""


class InjectionNotFoundError(DecisionServiceError):
    """No event batch or event membership matches the request."""


class InvalidCommitChoicesError(DecisionServiceError):
    """Commit choices do not form the exact gate membership partition."""


class OutcomeConflictError(DecisionServiceError):
    """A write-once event outcome conflicts with the requested transition."""


class DecisionStateError(DecisionServiceError):
    """Persisted event or memory state violates an already-established invariant."""


@dataclass(frozen=True, slots=True)
class _PlannedOutcome:
    event: Mapping[str, Any]
    desired: str | None
    is_new: bool


@dataclass(frozen=True, slots=True)
class _NeverRule:
    bias_step: float
    quarantine_kills: int


class DecisionService:
    """Own commit and feedback outcomes plus their atomic C.2 head changes."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def commit(self, command: CommitCommand) -> CommitResponse:
        """Commit one gate decision and render its frozen final block."""

        async with self._session_factory() as session:
            async with session.begin():
                await _lock_injection(session, command.injection_id)
                events = await _load_batch_for_update(session, command.injection_id)
                if not events:
                    if command.removed or command.added_back:
                        raise InjectionNotFoundError(str(command.injection_id))
                    return CommitResponse(final_block=render_final_block(()), wrong_removed=[])

                planned = _plan_commit(events, command)
                new_effects = [item for item in planned if item.is_new and item.desired]
                never_versions = {
                    _event_string(item.event, "scorer_version")
                    for item in new_effects
                    if item.desired == "removed:never"
                }
                never_rules = await _load_never_rules(session, never_versions)

                wrong_ids = {item.memory_id for item in command.removed if item.reason == "wrong"}
                effect_ids = {
                    _event_uuid(item.event, "memory_id")
                    for item in new_effects
                    if item.desired == "added_back" or item.desired.startswith("removed:")
                }
                heads = await _load_heads_for_update(session, effect_ids | wrong_ids)
                # Sample after the affected heads are locked so concurrent commits
                # cannot serialize in the opposite order and regress "last".
                commit_ts = await _database_clock(session)
                snapshots = {
                    memory_id: MemoryUnitSnapshot.from_row(row) for memory_id, row in heads.items()
                }

                for item in sorted(
                    new_effects, key=lambda value: _event_uuid(value.event, "memory_id").int
                ):
                    desired = item.desired
                    if desired != "added_back" and not desired.startswith("removed:"):
                        continue
                    memory_id = _event_uuid(item.event, "memory_id")
                    current = snapshots.get(memory_id)
                    if current is None:
                        raise DecisionStateError(f"memory {memory_id} does not exist")
                    changes = _commit_head_changes(
                        current,
                        desired=desired,
                        commit_ts=commit_ts,
                        never_rule=never_rules.get(_event_string(item.event, "scorer_version")),
                    )
                    snapshots[memory_id] = await cas_update_memory_unit(
                        session,
                        CasUpdate(
                            memory_id=memory_id,
                            expected_revision=current.revision,
                            rev_uid=mint_ulid(),
                            editor="system:inject",
                            origin_machine_id=_event_string(item.event, "machine_id"),
                            reason=f"inject/commit:{desired}",
                            changes=changes,
                        ),
                    )

                for item in new_effects:
                    await _write_new_outcome(session, item.event, item.desired)

                effective_events = [_effective_event(item) for item in planned]
                final_events = [
                    event for event in effective_events if event["outcome"] in _POSITIVE_OUTCOMES
                ]
                wrong_ranked = sorted(
                    (item for item in planned if item.desired == "removed:wrong"),
                    key=lambda item: _event_order(item.event),
                )
                wrong_removed = []
                for item in wrong_ranked:
                    memory_id = _event_uuid(item.event, "memory_id")
                    snapshot = snapshots.get(memory_id)
                    if snapshot is None:
                        raise DecisionStateError(f"memory {memory_id} does not exist")
                    wrong_removed.append(contract_memory_from_snapshot(snapshot))

                return CommitResponse(
                    final_block=render_final_block(final_events),
                    wrong_removed=wrong_removed,
                )

    async def feedback(self, command: FeedbackCommand) -> FeedbackResponse:
        """Record one exactly-once mid-thread feedback transition."""

        async with self._session_factory() as session:
            async with session.begin():
                await _lock_injection(session, command.injection_id)
                event = await _load_feedback_event_for_update(session, command)
                current_outcome = event["outcome"]
                if current_outcome == command.signal:
                    return FeedbackResponse(ok=True)
                allowed = (
                    (
                        command.signal == "mid_thread_added"
                        and (
                            current_outcome == "mid_thread_removed"
                            or (current_outcome is None and event["shown_as"] == "near_miss")
                        )
                    )
                    or (
                        command.signal == "mid_thread_removed"
                        and current_outcome
                        in {"kept", "added_back", "auto_entered", "mid_thread_added"}
                    )
                    or (
                        command.signal == "cited"
                        and current_outcome in {"kept", "added_back", "auto_entered"}
                    )
                )
                if not allowed:
                    raise OutcomeConflictError(
                        f"event outcome {current_outcome!r} cannot accept {command.signal}"
                    )

                if command.signal in {"mid_thread_removed", "cited"}:
                    memory_id = _event_uuid(event, "memory_id")
                    heads = await _load_heads_for_update(session, {memory_id})
                    row = heads.get(memory_id)
                    if row is None:
                        raise DecisionStateError(f"memory {memory_id} does not exist")
                    current = MemoryUnitSnapshot.from_row(row)
                    stat = "removals" if command.signal == "mid_thread_removed" else "citations"
                    stats = _increment_stat(current.stats, stat)
                    await cas_update_memory_unit(
                        session,
                        CasUpdate(
                            memory_id=memory_id,
                            expected_revision=current.revision,
                            rev_uid=mint_ulid(),
                            editor="system:feedback",
                            origin_machine_id=_event_string(event, "machine_id"),
                            reason=f"feedback/{command.signal}",
                            changes=MemoryUnitChanges(stats=stats),
                        ),
                    )

                await _replace_outcome(
                    session,
                    event,
                    expected=current_outcome,
                    desired=command.signal,
                )
                return FeedbackResponse(ok=True)


async def _lock_injection(session: AsyncSession, injection_id: UUID) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:injection_id, 4))"),
        {"injection_id": str(injection_id)},
    )


async def _load_batch_for_update(
    session: AsyncSession,
    injection_id: UUID,
) -> list[Mapping[str, Any]]:
    event = InjectionEvent.__table__
    return list(
        (
            await session.execute(
                select(*event.c)
                .where(event.c.injection_id == injection_id)
                .order_by(event.c.rank.asc(), event.c.memory_id.asc(), event.c.id.asc())
                .with_for_update()
            )
        )
        .mappings()
        .all()
    )


def _plan_commit(
    events: Sequence[Mapping[str, Any]],
    command: CommitCommand,
) -> list[_PlannedOutcome]:
    by_memory: dict[UUID, Mapping[str, Any]] = {}
    for event in events:
        if event["shown_as"] == "budget_cut":
            continue
        memory_id = _event_uuid(event, "memory_id")
        if memory_id in by_memory:
            raise DecisionStateError(f"injection contains duplicate memory {memory_id}")
        by_memory[memory_id] = event

    removed: dict[UUID, RemovalReason] = {}
    for item in command.removed:
        if item.memory_id in removed:
            raise InvalidCommitChoicesError(f"removed repeats memory {item.memory_id}")
        removed[item.memory_id] = item.reason
    added_back = set(command.added_back)
    if len(added_back) != len(command.added_back):
        raise InvalidCommitChoicesError("added_back contains duplicate memory IDs")
    overlap = set(removed) & added_back
    if overlap:
        raise InvalidCommitChoicesError(
            f"memory {min(overlap, key=lambda value: value.int)} is in both lists"
        )

    for memory_id, reason in removed.items():
        event = by_memory.get(memory_id)
        if event is None:
            raise InvalidCommitChoicesError(
                f"removed memory {memory_id} is not an injection batch member"
            )
        shown_as = event["shown_as"]
        if shown_as in {"injected", "pinned"}:
            continue
        if shown_as == "near_miss" and reason == "never":
            continue
        raise InvalidCommitChoicesError(
            f"removed memory {memory_id} cannot use reason {reason!r} when shown_as is {shown_as!r}"
        )
    for memory_id in added_back:
        event = by_memory.get(memory_id)
        if event is None or event["shown_as"] != "near_miss":
            raise InvalidCommitChoicesError(
                f"added_back memory {memory_id} is not a near-miss batch member"
            )

    planned = []
    for event in events:
        if event["shown_as"] == "budget_cut":
            continue
        memory_id = _event_uuid(event, "memory_id")
        shown_as = event["shown_as"]
        if shown_as == "near_miss":
            reason = removed.get(memory_id)
            desired = (
                "removed:never"
                if reason == "never"
                else ("added_back" if memory_id in added_back else None)
            )
        elif shown_as in {"injected", "pinned"}:
            reason = removed.get(memory_id)
            desired = f"removed:{reason}" if reason is not None else "kept"
        else:
            raise DecisionStateError(f"event has invalid shown_as {shown_as!r}")
        current = event["outcome"]
        if desired is None:
            if current is not None:
                raise OutcomeConflictError(
                    f"event for memory {memory_id} was already committed as {current}"
                )
            planned.append(_PlannedOutcome(event=event, desired=None, is_new=False))
            continue
        if current is None:
            planned.append(_PlannedOutcome(event=event, desired=desired, is_new=True))
            continue
        if current == desired or _is_feedback_descendant(event, current, desired):
            planned.append(_PlannedOutcome(event=event, desired=desired, is_new=False))
            continue
        raise OutcomeConflictError(
            f"event for memory {memory_id} is {current}; requested {desired}"
        )
    return planned


def _is_feedback_descendant(
    event: Mapping[str, Any],
    current: object,
    desired: str,
) -> bool:
    if current not in _FEEDBACK_OUTCOMES:
        return False
    base = "added_back" if event["shown_as"] == "near_miss" else "kept"
    return desired == base


async def _load_never_rules(
    session: AsyncSession,
    versions: set[str],
) -> dict[str, _NeverRule]:
    if not versions:
        return {}
    config = ScorerConfig.__table__
    rows = (
        (
            await session.execute(
                select(config.c.version, config.c.params).where(config.c.version.in_(versions))
            )
        )
        .mappings()
        .all()
    )
    by_version = {row["version"]: row for row in rows}
    rules: dict[str, _NeverRule] = {}
    for version in versions:
        row = by_version.get(version)
        if row is None or not isinstance(row["params"], Mapping):
            raise DecisionStateError(f"scorer_config {version!r} is missing or invalid")
        params = row["params"]
        step = params.get("never_bias_step")
        kills = params.get("quarantine_kills")
        if (
            isinstance(step, bool)
            or not isinstance(step, (int, float))
            or not math.isfinite(float(step))
            or float(step) >= 0.0
        ):
            raise DecisionStateError(f"scorer_config {version!r} never_bias_step is invalid")
        if isinstance(kills, bool) or not isinstance(kills, int) or kills <= 0:
            raise DecisionStateError(f"scorer_config {version!r} quarantine_kills is invalid")
        rules[version] = _NeverRule(float(step), kills)
    return rules


async def _database_clock(session: AsyncSession) -> datetime:
    value = await session.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime):  # pragma: no cover - PostgreSQL invariant
        raise DecisionStateError("database clock returned no timestamp")
    return value


async def _load_heads_for_update(
    session: AsyncSession,
    memory_ids: set[UUID],
) -> dict[UUID, Mapping[str, Any]]:
    if not memory_ids:
        return {}
    unit = MemoryUnit.__table__
    rows = (
        (
            await session.execute(
                select(*unit.c)
                .where(unit.c.id.in_(memory_ids))
                .order_by(unit.c.id.asc())
                .with_for_update()
            )
        )
        .mappings()
        .all()
    )
    return {row["id"]: row for row in rows}


def _commit_head_changes(
    current: MemoryUnitSnapshot,
    *,
    desired: str,
    commit_ts: datetime,
    never_rule: _NeverRule | None,
) -> MemoryUnitChanges:
    if desired == "added_back":
        stats = _increment_stat(current.stats, "injections")
        stats["last_injected_at"] = commit_ts.isoformat()
        return MemoryUnitChanges(stats=stats)

    stats = _increment_stat(current.stats, "removals")
    if desired != "removed:never":
        return MemoryUnitChanges(stats=stats)
    if never_rule is None:
        raise DecisionStateError("removed:never has no scorer-version rule")
    stats = _increment_stat(stats, "never_kills")
    status = current.status
    if status == "active" and stats["never_kills"] >= never_rule.quarantine_kills:
        status = "quarantined"
    return MemoryUnitChanges(
        stats=stats,
        bias=current.bias + never_rule.bias_step,
        status=status,
    )


def _increment_stat(stats_value: Mapping[str, Any], name: str) -> dict[str, Any]:
    stats = deepcopy(dict(stats_value))
    value = stats.get(name, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DecisionStateError(f"memory stats.{name} must be a nonnegative integer")
    stats[name] = value + 1
    return stats


async def _write_new_outcome(
    session: AsyncSession,
    event: Mapping[str, Any],
    desired: str,
) -> None:
    table = InjectionEvent.__table__
    result = await session.execute(
        update(table)
        .where(table.c.id == event["id"], table.c.outcome.is_(None))
        .values(outcome=desired)
    )
    if result.rowcount != 1:
        raise OutcomeConflictError(f"event {event['id']} outcome changed concurrently")


def _effective_event(item: _PlannedOutcome) -> dict[str, Any]:
    event = dict(item.event)
    if item.is_new:
        event["outcome"] = item.desired
    return event


async def _load_feedback_event_for_update(
    session: AsyncSession,
    command: FeedbackCommand,
) -> Mapping[str, Any]:
    event = InjectionEvent.__table__
    rows = (
        (
            await session.execute(
                select(*event.c)
                .where(
                    event.c.injection_id == command.injection_id,
                    event.c.memory_id == command.memory_id,
                )
                .with_for_update()
            )
        )
        .mappings()
        .all()
    )
    if not rows:
        raise InjectionNotFoundError(
            f"injection {command.injection_id} has no memory {command.memory_id}"
        )
    if len(rows) != 1:
        raise DecisionStateError("feedback membership is not unique")
    return rows[0]


async def _replace_outcome(
    session: AsyncSession,
    event: Mapping[str, Any],
    *,
    expected: str,
    desired: str,
) -> None:
    table = InjectionEvent.__table__
    result = await session.execute(
        update(table)
        .where(table.c.id == event["id"], table.c.outcome == expected)
        .values(outcome=desired)
    )
    if result.rowcount != 1:
        raise OutcomeConflictError(f"event {event['id']} outcome changed concurrently")


def _event_uuid(event: Mapping[str, Any], name: str) -> UUID:
    value = event.get(name)
    if not isinstance(value, UUID):
        raise DecisionStateError(f"event {name} must be a UUID")
    return value


def _event_string(event: Mapping[str, Any], name: str) -> str:
    value = event.get(name)
    if not isinstance(value, str):
        raise DecisionStateError(f"event {name} must be a string")
    return value


def _event_order(event: Mapping[str, Any]) -> tuple[int, int]:
    rank = event.get("rank")
    if isinstance(rank, bool) or not isinstance(rank, int):
        raise DecisionStateError("event rank must be an integer")
    return rank, _event_uuid(event, "memory_id").int


__all__ = [
    "CommitCommand",
    "DecisionService",
    "DecisionServiceError",
    "DecisionStateError",
    "FeedbackCommand",
    "InjectionNotFoundError",
    "InvalidCommitChoicesError",
    "OutcomeConflictError",
    "RemovedDecision",
]
