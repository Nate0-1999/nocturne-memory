"""Atomic, replay-safe writes for the append-only spend ledger."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spine.db.models import SpendEvent
from spine.spend.contracts import (
    ModelSpendRow,
    PurposeSpendRow,
    SpendEventInput,
    SpendTableSnapshot,
    ThreadSpendRow,
    event_values,
)

_PURPOSE_LABELS = {
    "building": "Building",
    "extraction": "Memory extraction",
    "curation": "Memory keeping",
    "judge": "Judging",
    "remember": "Remembering",
    "embedding": "Embeddings",
    "scout": "Verification",
}

_AGGREGATE_COLUMNS = """
COALESCE(sum(input_tokens), 0)::numeric AS input_tokens,
COALESCE(sum(kv_cache_tokens), 0)::numeric AS kv_cache_tokens,
COALESCE(sum(reasoning_tokens), 0)::numeric AS reasoning_tokens,
COALESCE(sum(output_tokens), 0)::numeric AS output_tokens,
sum(cost_usd) AS total_usd,
count(*)::bigint AS total_receipt_lines,
count(*) FILTER (WHERE cost_usd IS NULL)::bigint AS total_unpriced_lines,
sum(cost_usd) FILTER (WHERE in_window) AS spend_per_hour_usd,
count(*) FILTER (WHERE in_window)::bigint AS hourly_receipt_lines,
count(*) FILTER (WHERE in_window AND cost_usd IS NULL)::bigint AS hourly_unpriced_lines
"""


def _table_query(scoped: bool, principal_scoped: bool = False) -> str:
    filters = []
    if scoped:
        filters.append("thread_id = ANY(CAST(:thread_ids AS uuid[]))")
    if principal_scoped:
        filters.append("principal_id = :principal_id")
    scope_clause = "WHERE " + " AND ".join(filters) if filters else ""
    return f"""
WITH base AS (
    SELECT
        thread_id,
        model,
        purpose,
        cost_usd,
        ts >= :window_start AS in_window,
        CASE WHEN unit_of_measure = 'tokens' AND quantity_type = 'input_fresh'
            THEN quantity ELSE 0::numeric END AS input_tokens,
        CASE WHEN unit_of_measure = 'tokens'
                AND quantity_type IN ('input_cached', 'cache_write')
            THEN quantity ELSE 0::numeric END AS kv_cache_tokens,
        CASE WHEN unit_of_measure = 'tokens' AND quantity_type = 'reasoning'
            THEN quantity ELSE 0::numeric END AS reasoning_tokens,
        CASE WHEN unit_of_measure = 'tokens' AND quantity_type = 'output'
            THEN quantity ELSE 0::numeric END AS output_tokens
    FROM spend_event
    {scope_clause}
), grouped AS (
    SELECT
        'model'::text AS row_kind,
        thread_id,
        model,
        NULL::text AS purpose,
        {_AGGREGATE_COLUMNS}
    FROM base
    WHERE thread_id IS NOT NULL
    GROUP BY thread_id, model
    UNION ALL
    SELECT
        'thread'::text AS row_kind,
        thread_id,
        NULL::text AS model,
        NULL::text AS purpose,
        {_AGGREGATE_COLUMNS}
    FROM base
    WHERE thread_id IS NOT NULL
    GROUP BY thread_id
    UNION ALL
    SELECT
        'purpose'::text AS row_kind,
        NULL::uuid AS thread_id,
        NULL::text AS model,
        purpose,
        {_AGGREGATE_COLUMNS}
    FROM base
    WHERE thread_id IS NULL
    GROUP BY purpose
)
SELECT * FROM grouped
ORDER BY
    CASE row_kind WHEN 'model' THEN 0 WHEN 'thread' THEN 1 ELSE 2 END,
    thread_id NULLS LAST,
    model NULLS LAST,
    purpose NULLS LAST
"""


class SpendEventConflictError(RuntimeError):
    """An event_uid was replayed with a different normalized receipt line."""

    def __init__(self, event_uid: str) -> None:
        self.event_uid = event_uid
        super().__init__(f"spend event {event_uid} conflicts with its append-only receipt")


class SpendService:
    """Own the only write path into ADR-024's authoritative ledger."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def append(self, events: Sequence[SpendEventInput]) -> int:
        """Atomically insert or idempotently accept one nonempty receipt batch."""

        if not events:
            raise ValueError("spend receipt batch must not be empty")
        ids = [event.event_uid for event in events]
        if len(set(ids)) != len(ids):
            raise ValueError("spend receipt batch must have unique event_uid values")

        values = [event_values(event) for event in events]
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    postgresql_insert(SpendEvent)
                    .values(values)
                    .on_conflict_do_nothing(index_elements=[SpendEvent.event_uid])
                )
                rows = (
                    (await session.execute(select(SpendEvent).where(SpendEvent.event_uid.in_(ids))))
                    .scalars()
                    .all()
                )
                by_id = {row.event_uid: row for row in rows}
                for event in events:
                    row = by_id.get(event.event_uid)
                    if row is None:  # pragma: no cover - insert/select transaction invariant
                        raise RuntimeError(f"spend event {event.event_uid} was not persisted")
                    if _row_values(row) != event_values(event):
                        raise SpendEventConflictError(event.event_uid)
        return len(events)

    async def table(
        self,
        thread_ids: Sequence[UUID] | None = None,
        *,
        as_of: datetime | None = None,
        principal_id: str | None = None,
    ) -> SpendTableSnapshot:
        """Project the authoritative ledger into M3SP's money-only table."""

        instant = as_of or datetime.now(UTC)
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("spend table as_of must include a UTC offset")
        if thread_ids is not None and not thread_ids:
            return SpendTableSnapshot(as_of=instant, window_minutes=60, threads=[], purposes=[])

        scoped = thread_ids is not None
        parameters: dict[str, Any] = {"window_start": instant - timedelta(minutes=60)}
        if principal_id is not None:
            parameters["principal_id"] = principal_id
        if scoped:
            parameters["thread_ids"] = list(dict.fromkeys(thread_ids or ()))
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        text(_table_query(scoped, principal_id is not None)), parameters
                    )
                )
                .mappings()
                .all()
            )

        models: dict[UUID, list[ModelSpendRow]] = {}
        threads: list[ThreadSpendRow] = []
        purposes: list[PurposeSpendRow] = []
        for row in rows:
            metrics = _metric_values(row)
            if row["row_kind"] == "model":
                models.setdefault(row["thread_id"], []).append(
                    ModelSpendRow(model=row["model"], **metrics)
                )
            elif row["row_kind"] == "thread":
                threads.append(
                    ThreadSpendRow(
                        thread_id=row["thread_id"],
                        models=models.get(row["thread_id"], []),
                        **metrics,
                    )
                )
            else:
                purpose = row["purpose"]
                purposes.append(
                    PurposeSpendRow(
                        purpose=purpose,
                        label=_PURPOSE_LABELS[purpose],
                        **metrics,
                    )
                )
        return SpendTableSnapshot(
            as_of=instant,
            window_minutes=60,
            threads=threads,
            purposes=[] if scoped else purposes,
        )


def _row_values(row: SpendEvent) -> dict[str, Any]:
    return {
        "event_uid": row.event_uid,
        "ts": row.ts,
        "product_type": row.product_type,
        "quantity_type": row.quantity_type,
        "unit_of_measure": row.unit_of_measure,
        "quantity": row.quantity,
        "cost_usd": row.cost_usd,
        "basis": row.basis,
        "behavior": row.behavior,
        "purpose": row.purpose,
        "principal_id": row.principal_id,
        "machine_id": row.machine_id,
        "origin_agent": row.origin_agent,
        "thread_id": row.thread_id,
        "run_id": row.run_id,
        "prompt_id": row.prompt_id,
        "memory_id": row.memory_id,
        "model": row.model,
        "provider": row.provider,
        "quantization": row.quantization,
        "ref": row.ref,
        "meta": row.meta,
    }


def _metric_values(row: Mapping[str, Any]) -> dict[str, Decimal | int | None]:
    return {
        "input_tokens": row["input_tokens"],
        "kv_cache_tokens": row["kv_cache_tokens"],
        "reasoning_tokens": row["reasoning_tokens"],
        "output_tokens": row["output_tokens"],
        "total_usd": row["total_usd"],
        "total_receipt_lines": row["total_receipt_lines"],
        "total_unpriced_lines": row["total_unpriced_lines"],
        "spend_per_hour_usd": row["spend_per_hour_usd"],
        "hourly_receipt_lines": row["hourly_receipt_lines"],
        "hourly_unpriced_lines": row["hourly_unpriced_lines"],
    }


__all__ = ["SpendEventConflictError", "SpendService"]
