"""Append-only OpenRouter-to-ledger reconciliation for ADR-024."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Protocol

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spine.db.models import SpendEvent, SpendReconciliation
from spine.ids import mint_ulid

logger = logging.getLogger(__name__)
_USD = Decimal("0.000000000001")
_ADVISORY_LOCK_KEY = 240002013


class BrokerUnavailable(RuntimeError):
    """The broker could not provide a usable response."""


class InvalidBrokerResponse(RuntimeError):
    """The broker responded, but its usage observation was invalid."""


@dataclass(frozen=True, slots=True)
class BrokerUsage:
    """The key's cumulative usage and its usage for the current UTC day. [A-080]"""

    total_usd: Decimal
    day_usd: Decimal


class BrokerUsageGateway(Protocol):
    async def usage(self) -> BrokerUsage: ...


class OpenRouterUsageClient:
    """Read cumulative and today's usage for the already-configured OpenRouter key."""

    def __init__(self, *, api_key: str, base_url: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )

    async def usage(self) -> BrokerUsage:
        try:
            response = await self._client.get("/key")
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise BrokerUnavailable from error
        try:
            payload = json.loads(
                response.content,
                parse_float=Decimal,
                parse_int=Decimal,
            )
            return BrokerUsage(
                total_usd=_usage_value(payload["data"]["usage"]),
                day_usd=_usage_value(payload["data"]["usage_daily"]),
            )
        except (InvalidOperation, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise InvalidBrokerResponse from error

    async def aclose(self) -> None:
        await self._client.aclose()


class ReconciliationService:
    """Compare broker usage growth with cumulative priced LLM receipts."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        gateway: BrokerUsageGateway,
        *,
        tolerance_usd: Decimal,
    ) -> None:
        self._session_factory = session_factory
        self._gateway = gateway
        self._tolerance = _usd(tolerance_usd)
        if self._tolerance <= 0:
            raise ValueError("reconciliation tolerance must be positive")

    async def reconcile_once(self) -> SpendReconciliation:
        """Append one successful or safely unavailable observation. [A-037]"""

        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"),
                    {"key": _ADVISORY_LOCK_KEY},
                )
                ledger_cost, ledger_day, unpriced = await _ledger_totals(session)
                try:
                    broker = await self._gateway.usage()
                    row = await self._successful_row(
                        session,
                        broker_usage=_usd(broker.total_usd),
                        ledger_cost=ledger_cost,
                        unpriced=unpriced,
                    )
                    # A-080: today's bill beside today's ledger; A-037's status is unchanged.
                    row.broker_day_usd = _usd(broker.day_usd)
                    row.ledger_day_usd = ledger_day
                except BrokerUnavailable:
                    row = _unavailable_row("broker_unavailable", self._tolerance, unpriced)
                except (InvalidBrokerResponse, InvalidOperation, TypeError, ValueError):
                    row = _unavailable_row("invalid_broker_response", self._tolerance, unpriced)
                session.add(row)
            return row

    async def _successful_row(
        self,
        session: AsyncSession,
        *,
        broker_usage: Decimal,
        ledger_cost: Decimal,
        unpriced: int,
    ) -> SpendReconciliation:
        baseline = await session.scalar(
            select(SpendReconciliation)
            .where(SpendReconciliation.status != "unavailable")
            .order_by(SpendReconciliation.ts, SpendReconciliation.event_uid)
            .limit(1)
        )
        if baseline is None:
            return _row(
                status="baseline",
                broker_usage=broker_usage,
                ledger_cost=ledger_cost,
                broker_since=Decimal(0),
                ledger_since=Decimal(0),
                drift=Decimal(0),
                tolerance=self._tolerance,
                unpriced=unpriced,
            )
        assert baseline.broker_usage_usd is not None
        assert baseline.ledger_cost_usd is not None
        broker_since = _usd(broker_usage - baseline.broker_usage_usd)
        ledger_since = _usd(ledger_cost - baseline.ledger_cost_usd)
        if broker_since < 0 or ledger_since < 0:
            raise InvalidBrokerResponse
        drift = _usd(ledger_since - broker_since)
        return _row(
            status="drift" if abs(drift) > self._tolerance else "balanced",
            broker_usage=broker_usage,
            ledger_cost=ledger_cost,
            broker_since=broker_since,
            ledger_since=ledger_since,
            drift=drift,
            tolerance=self._tolerance,
            unpriced=unpriced,
        )


class ReconciliationScheduler:
    """Run reconciliation immediately and then at the configured cadence."""

    def __init__(self, service: ReconciliationService, *, interval_seconds: float) -> None:
        if interval_seconds <= 0:
            raise ValueError("reconciliation interval must be positive")
        self._service = service
        self._interval_seconds = interval_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("reconciliation scheduler is already running")
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="spend-reconciliation")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        await self._task
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._service.reconcile_once()
            except Exception:
                logger.exception("Spend reconciliation failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                pass


async def _ledger_totals(session: AsyncSession) -> tuple[Decimal, Decimal, int]:
    today = func.date_trunc("day", func.now(), "UTC")
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(SpendEvent.cost_usd), Decimal(0)),
                func.coalesce(
                    func.sum(SpendEvent.cost_usd).filter(SpendEvent.ts >= today), Decimal(0)
                ),
                func.count().filter(SpendEvent.cost_usd.is_(None)),
            ).where(SpendEvent.product_type.in_(("llm.request", "llm.embedding")))
        )
    ).one()
    return _usd(row[0]), _usd(row[1]), int(row[2])


def _usage_value(raw: object) -> Decimal:
    if isinstance(raw, bool):
        raise TypeError
    value = Decimal(raw) if isinstance(raw, str) else raw
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError
    return _usd(value)


def _row(
    *,
    status: str,
    broker_usage: Decimal,
    ledger_cost: Decimal,
    broker_since: Decimal,
    ledger_since: Decimal,
    drift: Decimal,
    tolerance: Decimal,
    unpriced: int,
) -> SpendReconciliation:
    return SpendReconciliation(
        event_uid=mint_ulid(),
        provider="openrouter",
        status=status,
        broker_usage_usd=_usd(broker_usage),
        ledger_cost_usd=_usd(ledger_cost),
        broker_since_baseline_usd=_usd(broker_since),
        ledger_since_baseline_usd=_usd(ledger_since),
        drift_usd=_usd(drift),
        tolerance_usd=tolerance,
        unpriced_lines=unpriced,
        error_code=None,
    )


def _unavailable_row(error_code: str, tolerance: Decimal, unpriced: int) -> SpendReconciliation:
    return SpendReconciliation(
        event_uid=mint_ulid(),
        provider="openrouter",
        status="unavailable",
        tolerance_usd=tolerance,
        unpriced_lines=unpriced,
        error_code=error_code,
    )


def _usd(value: Decimal) -> Decimal:
    return value.quantize(_USD, rounding=ROUND_HALF_EVEN)


__all__ = [
    "BrokerUnavailable",
    "BrokerUsage",
    "BrokerUsageGateway",
    "InvalidBrokerResponse",
    "OpenRouterUsageClient",
    "ReconciliationScheduler",
    "ReconciliationService",
]
