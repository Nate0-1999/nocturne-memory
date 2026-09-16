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
from spine.ids import mint_ulid
from spine.spend.contracts import (
    DailySpend,
    InfrastructureInvoice,
    InvoiceReceipt,
    MessageCache,
    ModelSpendRow,
    PurposeSpendRow,
    SpendEventInput,
    SpendRateLane,
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
    filters = ["ts <= :as_of"]
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

    async def invoice(self, principal_id: str, invoice: InfrastructureInvoice) -> int:
        """Serialize invoice identity, reusing the append-only receipt on retries."""
        ref = f"gcp-invoice:{invoice.invoice_id}"
        async with self._session_factory() as session, session.begin():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
                {"identity": f"{principal_id}:{ref}"},
            )
            existing = await session.scalar(
                select(SpendEvent).where(
                    SpendEvent.principal_id == principal_id,
                    SpendEvent.ref == ref,
                    SpendEvent.product_type == "infra.run.serve",
                )
            )
            receipt = InvoiceReceipt(
                event_uid=existing.event_uid if existing else mint_ulid(),
                ts=datetime.combine(invoice.invoice_date, datetime.min.time(), tzinfo=UTC),
                quantity_type="invoice",
                unit_of_measure="invoice",
                quantity=Decimal(1),
                cost_usd=invoice.amount_usd,
                basis="measured",
                behavior="fixed",
                purpose="building",
                principal_id=principal_id,
                provider="gcp",
                ref=ref,
                meta={"source": "manual_invoice", "invoice_id": invoice.invoice_id},
            )
            if existing:
                if _row_values(existing) != event_values(receipt):
                    raise SpendEventConflictError(existing.event_uid)
            else:
                session.add(SpendEvent(**event_values(receipt)))
        return 1

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
        parameters: dict[str, Any] = {
            "window_start": instant - timedelta(minutes=60),
            "as_of": instant,
        }
        if principal_id is not None:
            parameters["principal_id"] = principal_id
        if scoped:
            parameters["thread_ids"] = list(dict.fromkeys(thread_ids or ()))
        async with self._session_factory() as session:
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            rows = (
                (
                    await session.execute(
                        text(_table_query(scoped, principal_id is not None)), parameters
                    )
                )
                .mappings()
                .all()
            )
            rates, messages, days = await _history(
                session, parameters, scoped=scoped, principal_scoped=principal_id is not None
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
            rates=rates,
            rate_source=(
                "spend_event" if scoped or principal_id is not None else "v_spend_rate+spend_event"
            ),
            messages=messages,
            days=days,
        )


async def _history(
    session: AsyncSession, parameters: dict[str, Any], *, scoped: bool, principal_scoped: bool
) -> tuple[list[SpendRateLane], list[MessageCache], list[DailySpend]]:
    """ADR-024 / M3SR: scope ledger reads before grouping, preserving unknown prices."""
    filters = ["ts <= :as_of"]
    if scoped:
        filters.append("thread_id = ANY(CAST(:thread_ids AS uuid[]))")
    if principal_scoped:
        filters.append("principal_id = :principal_id")
    where = " AND ".join(filters)
    base = f"SELECT * FROM spend_event WHERE {where}"
    minute_rows = (
        "SELECT minute, purpose, model, cost_usd, receipt_lines, unpriced_lines "
        "FROM v_spend_rate WHERE minute >= :window_start AND minute <= :as_of"
        if not scoped and not principal_scoped
        else "SELECT date_trunc('minute', ts) AS minute, purpose, model, "
        "sum(cost_usd) AS cost_usd, count(*) AS receipt_lines, "
        "count(*) FILTER (WHERE cost_usd IS NULL) AS unpriced_lines "
        "FROM base WHERE ts >= :window_start GROUP BY minute, purpose, model"
    )
    rate_rows = (
        (
            await session.execute(
                text(f"""
        WITH base AS ({base}), minutes AS ({minute_rows}), lanes AS (
            SELECT minute, dimension, key, cost_usd, receipt_lines, unpriced_lines
            FROM minutes CROSS JOIN LATERAL (
                SELECT 'total'::text AS dimension, NULL::text AS key
                UNION ALL SELECT 'model', model
                UNION ALL SELECT 'curation', 'curation' WHERE purpose = 'curation'
            ) AS dimensions
            UNION ALL
            SELECT date_trunc('minute', ts),
                CASE WHEN origin_agent ~ '/root\\.|/[0-7][0-9A-HJKMNP-TV-Z]{{25}}$'
                    THEN 'subagent' ELSE 'agent' END,
                origin_agent, sum(cost_usd), count(*),
                count(*) FILTER (WHERE cost_usd IS NULL)
            FROM base WHERE ts >= :window_start AND product_type = 'llm.request'
            GROUP BY 1, 2, 3
        )
        SELECT dimension, key, minute, sum(cost_usd)::text AS cost_usd,
            sum(receipt_lines)::bigint AS receipt_lines,
            sum(unpriced_lines)::bigint AS unpriced_lines
        FROM lanes GROUP BY dimension, key, minute ORDER BY dimension, key, minute
    """),
                parameters,
            )
        )
        .mappings()
        .all()
    )
    grouped: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
    for row in rate_rows:
        grouped.setdefault((row["dimension"], row["key"]), []).append(
            {name: row[name] for name in ("minute", "cost_usd", "receipt_lines", "unpriced_lines")}
        )
    labels = {
        "total": "Total",
        "curation": "Memory curation",
        "agent": "Agent",
        "subagent": "Sub-agent",
        "model": "Model",
    }
    rates = [
        SpendRateLane(
            dimension=dimension,
            key=key,
            label=labels[dimension]
            + (f" · {key or 'unreported'}" if dimension not in {"total", "curation"} else ""),
            points=points,
        )
        for (dimension, key), points in grouped.items()
    ]
    cache_rows = (
        (
            await session.execute(
                text(f"""
        SELECT thread_id, prompt_id, min(ts) AS first_request_at,
            coalesce(sum(quantity) FILTER (WHERE quantity_type = 'input_fresh'), 0)::text
                AS fresh_tokens,
            coalesce(sum(quantity) FILTER (WHERE quantity_type = 'input_cached'), 0)::text
                AS cached_tokens,
            coalesce(sum(quantity) FILTER (WHERE quantity_type = 'cache_write'), 0)::text
                AS cache_write_tokens
        FROM ({base}) AS base WHERE product_type = 'llm.request'
            AND unit_of_measure = 'tokens' AND thread_id IS NOT NULL
        GROUP BY thread_id, prompt_id ORDER BY first_request_at, thread_id, prompt_id
    """),
                parameters,
            )
        )
        .mappings()
        .all()
    )
    day_rows = (
        (
            await session.execute(
                text(f"""
        SELECT date_trunc('day', ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' AS day,
            sum(cost_usd) FILTER (WHERE product_type LIKE 'llm.%')::text AS model_usd,
            sum(cost_usd) FILTER (WHERE product_type LIKE 'infra.%')::text AS infrastructure_usd,
            sum(cost_usd)::text AS total_usd,
            count(*) FILTER (WHERE cost_usd IS NULL)::int AS unpriced_lines
        FROM ({base}) AS base GROUP BY day ORDER BY day
    """),
                parameters,
            )
        )
        .mappings()
        .all()
    )
    return (
        rates,
        [MessageCache(**row) for row in cache_rows],
        [DailySpend(**row) for row in day_rows],
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
