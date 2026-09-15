"""Build one honest A-028 snapshot from canonical read sources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import and_

from spine.db.models import ApprovalQueueItem, MemoryEdge, MemoryUnit, SpendReconciliation
from spine.vitals.contracts import (
    LifecycleRate,
    PalaceCount,
    ReconciliationSnapshot,
    ResourceSnapshot,
    SpendDimension,
    SpendLane,
    SpendPoint,
    SpendSnapshot,
    VitalsSnapshot,
)

_WINDOW_MINUTES = 60
_WINDOW = timedelta(minutes=_WINDOW_MINUTES)
_UNREPORTED_MODEL_KEY = "unreported"
_MODEL_KEY_ESCAPE = "~"

_NOT_RECORDED_LIFECYCLE = (
    "reinforced",
    "superseded",
    "merged",
    "quarantined",
    "tombstoned",
    "add_backs",
)
_NOT_RECORDED_COUNTS = ("staged_units",)


@dataclass(slots=True)
class _PointTotal:
    cost_usd: Decimal = Decimal(0)
    has_priced_line: bool = False
    receipt_lines: int = 0
    unpriced_lines: int = 0

    def add(self, *, cost_usd: Decimal | None, receipt_lines: int, unpriced_lines: int) -> None:
        if receipt_lines < 0 or unpriced_lines < 0 or unpriced_lines > receipt_lines:
            raise ValueError("v_spend_rate returned impossible receipt counts")
        if cost_usd is not None:
            if not cost_usd.is_finite() or cost_usd < 0:
                raise ValueError("v_spend_rate returned an invalid cost")
            self.cost_usd += cost_usd
            self.has_priced_line = True
        self.receipt_lines += receipt_lines
        self.unpriced_lines += unpriced_lines


class VitalsService:
    """Read the canonical spend view and the three enacted memory gauges."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        reconciliation_configured: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._reconciliation_configured = reconciliation_configured

    async def snapshot(
        self, *, thread_id: UUID | None = None, principal_id: str | None = None
    ) -> VitalsSnapshot:
        """Return one repeatable-read trailing-hour snapshot without refreshing views."""

        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                )
                as_of = await session.scalar(select(func.transaction_timestamp()))
                if not isinstance(as_of, datetime) or as_of.tzinfo is None:
                    raise RuntimeError("Postgres returned an invalid snapshot timestamp")
                window_start = as_of - _WINDOW
                spend_rows = await _spend_rows(
                    session,
                    window_start=window_start,
                    as_of=as_of,
                    thread_id=thread_id,
                    principal_id=principal_id,
                )
                memory_scope = (
                    [MemoryUnit.principal_id == principal_id] if principal_id is not None else []
                )
                created_per_hour = await session.scalar(
                    select(func.count())
                    .select_from(MemoryUnit)
                    .where(
                        *memory_scope,
                        MemoryUnit.status != "staged",
                        MemoryUnit.created_at > window_start,
                        MemoryUnit.created_at <= as_of,
                    )
                )
                counts = (
                    await session.execute(
                        select(
                            func.count()
                            .filter(MemoryUnit.status == "active")
                            .label("active_units"),
                            func.count()
                            .filter(
                                and_(
                                    MemoryUnit.status == "active",
                                    MemoryUnit.pin.is_(True),
                                )
                            )
                            .label("pinned_units"),
                            func.count()
                            .filter(MemoryUnit.status == "candidate")
                            .label("candidates_pending"),
                        ).where(*memory_scope)
                    )
                ).one()
                edges = select(func.count()).select_from(MemoryEdge)
                if principal_id is not None:
                    owned_ids = select(MemoryUnit.id).where(*memory_scope)
                    edges = edges.where(
                        MemoryEdge.from_memory_id.in_(owned_ids),
                        MemoryEdge.to_memory_id.in_(owned_ids),
                    )
                edge_count = await session.scalar(edges)
                queue_depth = await session.scalar(
                    select(func.count())
                    .select_from(ApprovalQueueItem)
                    .where(ApprovalQueueItem.state == "pending")
                    .where(*(
                        [ApprovalQueueItem.principal_id == principal_id]
                        if principal_id is not None else []
                    ))
                )
                reconciliation = None if principal_id is not None else await session.scalar(
                    select(SpendReconciliation)
                    .order_by(SpendReconciliation.ts.desc(), SpendReconciliation.event_uid.desc())
                    .limit(1)
                )
                database_bytes = None if principal_id is not None else await session.scalar(
                    select(func.pg_database_size(func.current_database()))
                )

        return VitalsSnapshot(
            as_of=as_of,
            window_minutes=_WINDOW_MINUTES,
            spend=_spend_snapshot(
                spend_rows,
                source=(
                    "spend_event" if thread_id is not None or principal_id is not None
                    else "v_spend_rate"
                ),
            ),
            reconciliation=_reconciliation_snapshot(
                reconciliation,
                configured=self._reconciliation_configured and principal_id is None,
            ),
            resources=ResourceSnapshot(
                status="partial",
                daemon_rss_bytes=None,
                daemon_uptime_seconds=None,
                disk_free_bytes=None,
                disk_total_bytes=None,
                database_bytes=(
                    None if database_bytes is None
                    else _nonnegative_count(database_bytes, "database_bytes")
                ),
                journal_bytes=None,
                backup_bytes=None,
                warning=None,
            ),
            lifecycle_rates=[
                LifecycleRate(
                    metric="created",
                    status="measured",
                    per_hour=_nonnegative_count(created_per_hour, "created"),
                    source="memory_unit.created_at",
                ),
                *[
                    LifecycleRate(
                        metric=metric,
                        status="not_recorded",
                        per_hour=None,
                        source=None,
                    )
                    for metric in _NOT_RECORDED_LIFECYCLE
                ],
            ],
            palace_counts=[
                PalaceCount(
                    metric="active_units",
                    status="measured",
                    count=_nonnegative_count(counts.active_units, "active_units"),
                    source="memory_unit.status",
                ),
                PalaceCount(
                    metric="pinned_units",
                    status="measured",
                    count=_nonnegative_count(counts.pinned_units, "pinned_units"),
                    source="memory_unit.status+pin",
                ),
                PalaceCount(
                    metric="candidates_pending",
                    status="measured",
                    count=_nonnegative_count(counts.candidates_pending, "candidates_pending"),
                    source="memory_unit.status",
                ),
                PalaceCount(
                    metric="edges",
                    status="measured",
                    count=_nonnegative_count(edge_count, "edges"),
                    source="memory_edge",
                ),
                *[
                    PalaceCount(
                        metric=metric,
                        status="not_recorded",
                        count=None,
                        source=None,
                    )
                    for metric in _NOT_RECORDED_COUNTS
                ],
                PalaceCount(
                    metric="queue_depth",
                    status="measured",
                    count=_nonnegative_count(queue_depth, "queue_depth"),
                    source="approval_queue_item.state",
                ),
            ],
        )


async def _spend_rows(
    session: AsyncSession,
    *,
    window_start: datetime,
    as_of: datetime,
    thread_id: UUID | None,
    principal_id: str | None = None,
) -> list[Any]:
    if thread_id is None and principal_id is None:
        statement = text(
            "SELECT minute, purpose, model, provider, receipt_lines, "
            "cost_usd, unpriced_lines FROM v_spend_rate "
            "WHERE minute > :window_start AND minute <= :as_of "
            "ORDER BY minute ASC, purpose ASC, model ASC NULLS FIRST, "
            "provider ASC NULLS FIRST"
        )
        parameters = {"window_start": window_start, "as_of": as_of}
    else:
        filters = ["ts > :window_start", "ts <= :as_of"]
        parameters = {"window_start": window_start, "as_of": as_of}
        if thread_id is not None:
            filters.append("thread_id = :thread_id")
            parameters["thread_id"] = thread_id
        if principal_id is not None:
            filters.append("principal_id = :principal_id")
            parameters["principal_id"] = principal_id
        statement = text(
            "SELECT date_trunc('minute', ts) AS minute, purpose, model, provider, "
            "count(*)::bigint AS receipt_lines, sum(cost_usd) AS cost_usd, "
            "count(*) FILTER (WHERE cost_usd IS NULL)::bigint AS unpriced_lines "
            "FROM spend_event WHERE " + " AND ".join(filters) + " "
            "GROUP BY minute, purpose, model, provider "
            "ORDER BY minute ASC, purpose ASC, model ASC NULLS FIRST, "
            "provider ASC NULLS FIRST"
        )
    return (await session.execute(statement, parameters)).mappings().all()


def _spend_snapshot(
    rows: list[Any],
    *,
    source: Literal["v_spend_rate", "spend_event"] = "v_spend_rate",
) -> SpendSnapshot:
    total: dict[datetime, _PointTotal] = {}
    purposes: dict[str, dict[datetime, _PointTotal]] = {}
    models: dict[str, dict[datetime, _PointTotal]] = {}
    latest_minute: datetime | None = None

    for row in rows:
        minute = row["minute"]
        purpose = row["purpose"]
        model = row["model"]
        receipt_lines = _nonnegative_count(row["receipt_lines"], "receipt_lines")
        unpriced_lines = _nonnegative_count(row["unpriced_lines"], "unpriced_lines")
        cost_usd = row["cost_usd"]
        if not isinstance(minute, datetime) or minute.tzinfo is None:
            raise ValueError("v_spend_rate returned an invalid minute")
        if not isinstance(purpose, str) or not purpose:
            raise ValueError("v_spend_rate returned an invalid purpose")
        if model is not None and (not isinstance(model, str) or not model):
            raise ValueError("v_spend_rate returned an invalid model")
        if cost_usd is not None and not isinstance(cost_usd, Decimal):
            raise ValueError("v_spend_rate returned a non-decimal cost")

        model_key = _model_lane_key(model)
        for points in (
            total,
            purposes.setdefault(purpose, {}),
            models.setdefault(model_key, {}),
        ):
            points.setdefault(minute, _PointTotal()).add(
                cost_usd=cost_usd,
                receipt_lines=receipt_lines,
                unpriced_lines=unpriced_lines,
            )
        if latest_minute is None or minute > latest_minute:
            latest_minute = minute

    lanes = [_lane("total", None, "All spend", total)]
    lanes.extend(_lane("purpose", key, key, purposes[key]) for key in sorted(purposes))
    lanes.extend(
        _lane(
            "model",
            model_key,
            _model_lane_label(model_key),
            models[model_key],
        )
        for model_key in sorted(models)
    )
    return SpendSnapshot(
        source_view=source,
        latest_minute=latest_minute,
        lanes=lanes,
    )


def _model_lane_key(model: str | None) -> str:
    if model is None:
        return _UNREPORTED_MODEL_KEY
    if model == _UNREPORTED_MODEL_KEY or model.startswith(_MODEL_KEY_ESCAPE):
        return f"{_MODEL_KEY_ESCAPE}{model}"
    return model


def _model_lane_label(key: str) -> str:
    if key == _UNREPORTED_MODEL_KEY:
        return "Model not reported"
    if key.startswith(_MODEL_KEY_ESCAPE):
        return key.removeprefix(_MODEL_KEY_ESCAPE)
    return key


def _lane(
    dimension: SpendDimension,
    key: str | None,
    label: str,
    totals: dict[datetime, _PointTotal],
) -> SpendLane:
    return SpendLane(
        dimension=dimension,
        key=key,
        label=label,
        points=[
            SpendPoint(
                minute=minute,
                cost_usd=(format(total.cost_usd, "f") if total.has_priced_line else None),
                receipt_lines=total.receipt_lines,
                unpriced_lines=total.unpriced_lines,
            )
            for minute, total in sorted(totals.items())
        ],
    )


def _nonnegative_count(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"database returned an invalid {name} count")
    return value


def _reconciliation_snapshot(
    row: SpendReconciliation | None,
    *,
    configured: bool,
) -> ReconciliationSnapshot:
    source = "openrouter:/api/v1/key" if configured else None
    if row is None:
        return ReconciliationSnapshot(
            status="not_recorded",
            checked_at=None,
            broker_usage_usd=None,
            ledger_cost_usd=None,
            broker_since_baseline_usd=None,
            ledger_since_baseline_usd=None,
            drift_usd=None,
            tolerance_usd=None,
            unpriced_lines=0,
            source=source,
            error_code=None,
        )
    return ReconciliationSnapshot(
        status=row.status,
        checked_at=row.ts,
        broker_usage_usd=_decimal_text(row.broker_usage_usd),
        ledger_cost_usd=_decimal_text(row.ledger_cost_usd),
        broker_since_baseline_usd=_decimal_text(row.broker_since_baseline_usd),
        ledger_since_baseline_usd=_decimal_text(row.ledger_since_baseline_usd),
        drift_usd=_decimal_text(row.drift_usd),
        tolerance_usd=_decimal_text(row.tolerance_usd),
        unpriced_lines=row.unpriced_lines,
        source="openrouter:/api/v1/key",
        error_code=row.error_code,
    )


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


__all__ = ["VitalsService"]
