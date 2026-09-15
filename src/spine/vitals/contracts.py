"""Exact A-028 response contract for the live Palace Vitals snapshot."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    model_validator,
)


def _nonblank(value: str) -> str:
    if not value.strip() or value != value.strip():
        raise ValueError("value must be nonblank without surrounding whitespace")
    return value


type NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
type NonBlankString = Annotated[StrictStr, AfterValidator(_nonblank)]
type DecimalString = Annotated[
    StrictStr,
    Field(pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"),
]
type SignedDecimalString = Annotated[
    StrictStr,
    Field(pattern=r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"),
]
type GaugeStatus = Literal["measured", "not_recorded", "placeholder"]
type SpendDimension = Literal["total", "purpose", "model"]
type LifecycleMetric = Literal[
    "created",
    "reinforced",
    "superseded",
    "merged",
    "quarantined",
    "tombstoned",
    "add_backs",
]
type PalaceMetric = Literal[
    "active_units",
    "pinned_units",
    "candidates_pending",
    "edges",
    "staged_units",
    "queue_depth",
]


class VitalsContract(BaseModel):
    """Closed immutable object at the A-028 read boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SpendPoint(VitalsContract):
    minute: AwareDatetime
    cost_usd: DecimalString | None
    receipt_lines: NonNegativeInt
    unpriced_lines: NonNegativeInt

    @model_validator(mode="after")
    def require_possible_unpriced_count(self) -> SpendPoint:
        if self.unpriced_lines > self.receipt_lines:
            raise ValueError("unpriced_lines cannot exceed receipt_lines")
        return self


class SpendLane(VitalsContract):
    dimension: SpendDimension
    key: NonBlankString | None
    label: NonBlankString
    points: list[SpendPoint]

    @model_validator(mode="after")
    def require_dimension_key(self) -> SpendLane:
        if self.dimension == "total" and self.key is not None:
            raise ValueError("the total spend lane must have a null key")
        if self.dimension != "total" and self.key is None:
            raise ValueError("a dimensioned spend lane must have a key")
        return self


class SpendSnapshot(VitalsContract):
    source_view: Literal["v_spend_rate", "spend_event"]
    latest_minute: AwareDatetime | None
    lanes: list[SpendLane]


class ReconciliationSnapshot(VitalsContract):
    status: Literal["not_recorded", "baseline", "balanced", "drift", "unavailable"]
    checked_at: AwareDatetime | None
    broker_usage_usd: DecimalString | None
    ledger_cost_usd: DecimalString | None
    broker_since_baseline_usd: DecimalString | None
    ledger_since_baseline_usd: DecimalString | None
    drift_usd: SignedDecimalString | None
    tolerance_usd: DecimalString | None
    unpriced_lines: NonNegativeInt
    source: Literal["openrouter:/api/v1/key"] | None
    error_code: Literal["broker_unavailable", "invalid_broker_response"] | None

    @model_validator(mode="after")
    def require_honest_shape(self) -> ReconciliationSnapshot:
        observations = (
            self.broker_usage_usd,
            self.ledger_cost_usd,
            self.broker_since_baseline_usd,
            self.ledger_since_baseline_usd,
            self.drift_usd,
        )
        if self.status == "not_recorded":
            if self.checked_at is not None or any(value is not None for value in observations):
                raise ValueError("not_recorded reconciliation cannot contain observations")
            if self.tolerance_usd is not None or self.error_code is not None:
                raise ValueError("not_recorded reconciliation cannot contain a result")
        elif self.status == "unavailable":
            if self.checked_at is None or any(value is not None for value in observations):
                raise ValueError("unavailable reconciliation has no numeric observations")
            if self.tolerance_usd is None or self.error_code is None:
                raise ValueError("unavailable reconciliation requires tolerance and error code")
        else:
            if self.checked_at is None or any(value is None for value in observations):
                raise ValueError("successful reconciliation requires every observation")
            if self.tolerance_usd is None or self.error_code is not None:
                raise ValueError("successful reconciliation has tolerance and no error")
        if self.source is None and self.status != "not_recorded":
            raise ValueError("recorded reconciliation requires a source")
        return self


class LifecycleRate(VitalsContract):
    metric: LifecycleMetric
    status: GaugeStatus
    per_hour: NonNegativeInt | None
    source: NonBlankString | None

    @model_validator(mode="after")
    def require_honest_availability(self) -> LifecycleRate:
        _require_gauge_availability(
            metric=self.metric,
            status=self.status,
            value=self.per_hour,
            source=self.source,
        )
        return self


class PalaceCount(VitalsContract):
    metric: PalaceMetric
    status: GaugeStatus
    count: NonNegativeInt | None
    source: NonBlankString | None

    @model_validator(mode="after")
    def require_honest_availability(self) -> PalaceCount:
        _require_gauge_availability(
            metric=self.metric,
            status=self.status,
            value=self.count,
            source=self.source,
        )
        return self


class ResourceSnapshot(VitalsContract):
    """Database-owned portion of the cross-process A-044 resource gauge."""

    status: Literal["partial", "measured"]
    daemon_rss_bytes: NonNegativeInt | None
    daemon_uptime_seconds: NonNegativeInt | None
    disk_free_bytes: NonNegativeInt | None
    disk_total_bytes: NonNegativeInt | None
    database_bytes: NonNegativeInt | None
    journal_bytes: NonNegativeInt | None
    backup_bytes: NonNegativeInt | None
    warning: Literal["low_disk"] | None

    @model_validator(mode="after")
    def require_honest_availability(self) -> ResourceSnapshot:
        local = (
            self.daemon_rss_bytes,
            self.daemon_uptime_seconds,
            self.disk_free_bytes,
            self.disk_total_bytes,
            self.journal_bytes,
            self.backup_bytes,
        )
        if self.status == "measured" and any(value is None for value in local):
            raise ValueError("measured resources require every local observation")
        if self.warning is not None and (
            self.disk_free_bytes is None or self.disk_total_bytes is None
        ):
            raise ValueError("a resource warning requires disk observations")
        return self


class VitalsSnapshot(VitalsContract):
    as_of: AwareDatetime
    window_minutes: Literal[60]
    spend: SpendSnapshot
    reconciliation: ReconciliationSnapshot
    resources: ResourceSnapshot
    lifecycle_rates: list[LifecycleRate]
    palace_counts: list[PalaceCount]


def _require_gauge_availability(
    *,
    metric: LifecycleMetric | PalaceMetric,
    status: GaugeStatus,
    value: int | None,
    source: str | None,
) -> None:
    if status == "measured":
        if value is None or source is None:
            raise ValueError("a measured gauge requires a value and source")
        return
    if value is not None or source is not None:
        raise ValueError("an unavailable gauge must have null value and source")


__all__ = [
    "GaugeStatus",
    "LifecycleMetric",
    "LifecycleRate",
    "PalaceCount",
    "PalaceMetric",
    "ReconciliationSnapshot",
    "ResourceSnapshot",
    "SpendDimension",
    "SpendLane",
    "SpendPoint",
    "SpendSnapshot",
    "VitalsSnapshot",
]
