"""Exact M2A wire contracts for ADR-024 receipt lines."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictStr,
    field_serializer,
    field_validator,
    model_validator,
)

from spine.ids import normalize_ulid
from spine.vitals.contracts import DecimalString, SpendPoint


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    if value != value.strip():
        raise ValueError("value must not contain surrounding whitespace")
    return value


type ULID = Annotated[StrictStr, AfterValidator(normalize_ulid)]
type NonBlankString = Annotated[StrictStr, AfterValidator(_nonblank)]
type SpendProductType = Literal["llm.request", "llm.embedding"]
type SpendBasis = Literal["measured", "allocated", "estimated"]
type SpendBehavior = Literal["variable", "fixed", "step"]
type SpendPurpose = Literal[
    "building",
    "extraction",
    "curation",
    "judge",
    "remember",
    "embedding",
    "scout",
]


class SpendContract(BaseModel):
    """Closed, finite JSON object used at the spend boundary."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


class SpendEventInput(SpendContract):
    """One row-reads-as-a-sentence ADR-024 receipt line."""

    event_uid: ULID
    ts: datetime
    product_type: SpendProductType
    quantity_type: NonBlankString
    unit_of_measure: NonBlankString
    quantity: Decimal = Field(gt=0, max_digits=30, decimal_places=9)
    cost_usd: Decimal | None = Field(default=None, ge=0, max_digits=20, decimal_places=12)
    basis: SpendBasis
    behavior: SpendBehavior
    purpose: SpendPurpose
    principal_id: NonBlankString | None = None
    machine_id: NonBlankString | None = None
    origin_agent: NonBlankString | None = None
    thread_id: UUID | None = None
    run_id: ULID | None = None
    prompt_id: ULID | None = None
    memory_id: UUID | None = None
    model: NonBlankString | None = None
    provider: NonBlankString | None = None
    quantization: NonBlankString | None = None
    ref: NonBlankString
    meta: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("ts")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("ts must include a UTC offset")
        return value


class SpendEventsRequest(SpendContract):
    """One atomic receipt batch for a provider response."""

    events: list[SpendEventInput] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def require_unique_event_ids(self) -> SpendEventsRequest:
        event_ids = [event.event_uid for event in self.events]
        if len(set(event_ids)) != len(event_ids):
            raise ValueError("events must have unique event_uid values")
        return self


class SpendEventsResponse(SpendContract):
    """Idempotent acceptance count, including identical replays."""

    accepted: int = Field(strict=True, ge=1)


class SpendTableMetrics(SpendContract):
    """Exact token lanes plus honest known-cost state for one ledger grouping."""

    input_tokens: Decimal = Field(ge=0, max_digits=30, decimal_places=9)
    kv_cache_tokens: Decimal = Field(ge=0, max_digits=30, decimal_places=9)
    reasoning_tokens: Decimal = Field(ge=0, max_digits=30, decimal_places=9)
    output_tokens: Decimal = Field(ge=0, max_digits=30, decimal_places=9)
    total_usd: Decimal | None = Field(default=None, ge=0, max_digits=20, decimal_places=12)
    total_receipt_lines: int = Field(strict=True, ge=0)
    total_unpriced_lines: int = Field(strict=True, ge=0)
    spend_per_hour_usd: Decimal | None = Field(
        default=None, ge=0, max_digits=20, decimal_places=12
    )
    hourly_receipt_lines: int = Field(strict=True, ge=0)
    hourly_unpriced_lines: int = Field(strict=True, ge=0)

    @field_serializer(
        "input_tokens",
        "kv_cache_tokens",
        "reasoning_tokens",
        "output_tokens",
        "total_usd",
        "spend_per_hour_usd",
        when_used="json",
    )
    def serialize_exact_decimal(self, value: Decimal | None):
        """Keep exact decimals plain on the wire, including sub-cent receipt totals."""

        return None if value is None else format(value, "f")

    @model_validator(mode="after")
    def require_honest_costs(self) -> SpendTableMetrics:
        _require_honest_cost(
            self.total_usd, self.total_receipt_lines, self.total_unpriced_lines
        )
        _require_honest_cost(
            self.spend_per_hour_usd,
            self.hourly_receipt_lines,
            self.hourly_unpriced_lines,
        )
        return self


class ModelSpendRow(SpendTableMetrics):
    model: NonBlankString | None


class ThreadSpendRow(SpendTableMetrics):
    thread_id: UUID
    models: list[ModelSpendRow]


class PurposeSpendRow(SpendTableMetrics):
    purpose: SpendPurpose
    label: NonBlankString


class SpendRateLane(SpendContract):
    dimension: Literal["total", "agent", "subagent", "model", "curation"]
    key: str | None
    label: NonBlankString
    points: list[SpendPoint]


class MessageCache(SpendContract):
    thread_id: UUID
    prompt_id: str | None
    first_request_at: datetime
    fresh_tokens: DecimalString
    cached_tokens: DecimalString
    cache_write_tokens: DecimalString


class DailySpend(SpendContract):
    day: datetime
    model_usd: DecimalString | None
    infrastructure_usd: DecimalString | None
    total_usd: DecimalString | None
    unpriced_lines: int = Field(ge=0)


class SpendTableSnapshot(SpendContract):
    as_of: datetime
    window_minutes: Literal[60]
    threads: list[ThreadSpendRow]
    purposes: list[PurposeSpendRow]
    rates: list[SpendRateLane] = Field(default_factory=list)
    rate_source: Literal["v_spend_rate+spend_event", "spend_event"] = "spend_event"
    messages: list[MessageCache] = Field(default_factory=list)
    days: list[DailySpend] = Field(default_factory=list)

    @field_validator("as_of")
    @classmethod
    def require_aware_as_of(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("as_of must include a UTC offset")
        return value


def _require_honest_cost(
    cost: Decimal | None,
    receipt_lines: int,
    unpriced_lines: int,
) -> None:
    if unpriced_lines > receipt_lines:
        raise ValueError("unpriced receipt lines cannot exceed receipt lines")
    all_unpriced = receipt_lines == unpriced_lines
    if all_unpriced and receipt_lines > 0 and cost is not None:
        raise ValueError("an all-unpriced row must have null cost")
    if not all_unpriced and cost is None:
        raise ValueError("a row with priced receipt lines must carry known cost")


def event_values(event: SpendEventInput) -> dict[str, Any]:
    """Return database-ready values without lossy JSON number conversion."""

    return event.model_dump(mode="python")


__all__ = [
    "SpendBasis",
    "SpendBehavior",
    "SpendContract",
    "SpendEventInput",
    "SpendEventsRequest",
    "SpendEventsResponse",
    "SpendTableMetrics",
    "SpendTableSnapshot",
    "ModelSpendRow",
    "ThreadSpendRow",
    "PurposeSpendRow",
    "SpendProductType",
    "SpendPurpose",
    "event_values",
]
