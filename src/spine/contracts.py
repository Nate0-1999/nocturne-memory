"""Shared strict models for the exact SPEC C.4 wire contract."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StrictStr,
    model_validator,
)

from spine.ids import normalize_ulid

MemoryKind = Literal["fact", "preference", "procedure", "project_note", "persona", "pinned"]
MemoryStatus = Literal["active", "candidate", "quarantined", "tombstoned"]


def _nonblank(value: str) -> str:
    if not value.strip() or value != value.strip():
        raise ValueError("value must be nonblank without surrounding whitespace")
    return value


type NonBlankString = Annotated[StrictStr, AfterValidator(_nonblank)]
type ULID = Annotated[StrictStr, AfterValidator(normalize_ulid)]


class ContractModel(BaseModel):
    """Reject fields outside the literal cross-repository contract."""

    model_config = ConfigDict(extra="forbid")


class ContractRequest(ContractModel):
    """Marker base for exact C.4 request bodies."""


class MemoryFeatures(ContractModel):
    sem: float
    kw: float
    time: float
    proj: float
    freq: float
    hist: float
    loc: float | None = None
    thread: float | None = None
    where: float | None = None
    axes: dict[str, float] = Field(default_factory=dict, exclude_if=lambda value: not value)


class MemoryCard(ContractModel):
    memory_id: UUID
    label: str
    body: str
    kind: MemoryKind
    pin: bool
    score: float
    features: MemoryFeatures | None
    rank: int | None


class ScoredMemoryCard(MemoryCard):
    """Inject/prepare card, where C.4 requires scoring details."""

    features: MemoryFeatures
    rank: int


class SimilarityMemoryCard(MemoryCard):
    """Dedup/search card, where C.4 requires scoring details to be null."""

    features: None
    rank: None


class MemoryUnit(ContractModel):
    memory_id: UUID
    principal_id: str
    label: str
    body: str
    kind: MemoryKind
    keywords: list[str]
    project_key: str | None
    thread_origin: str | None
    origin_thread_id: UUID | None
    origin_path: str | None
    origin_location: str | None = None
    pin: bool
    status: MemoryStatus
    revision: int
    stats: dict[str, Any]
    bias: float
    embedding_model: str
    created_at: datetime
    updated_at: datetime


class MemoryAllocation(ContractModel):
    memory_context_share: float = Field(ge=0.01, le=0.50)
    share_tokens: int = Field(ge=0)
    regular_tokens: int = Field(ge=0)
    pinned_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    pinned_overflow_tokens: int = Field(ge=0)


class PrepareResponse(ContractModel):
    injection_id: UUID
    snapshot_ts: datetime
    scorer_version: str
    injected: list[ScoredMemoryCard]
    near_misses: list[ScoredMemoryCard]
    final_block: str | None
    memory_allocation: MemoryAllocation


class CommitResponse(ContractModel):
    final_block: str
    wrong_removed: list[MemoryUnit]


class RestoredInjection(ContractModel):
    prepared: PrepareResponse
    confirmed_memory_ids: list[UUID]
    excluded_memory_ids: list[UUID]
    event_sources: dict[UUID, UUID]
    pending: bool


class FeedbackResponse(ContractModel):
    ok: Literal[True]


class InjectionEventAnnotationInput(ContractRequest):
    """One guarded A-053 verification-only classification request."""

    target_event_uid: ULID
    expected_principal_id: StrictStr
    expected_machine_id: StrictStr
    reason: NonBlankString
    annotator_principal_id: NonBlankString
    annotator_machine_id: NonBlankString
    annotator_origin_agent: NonBlankString


class InjectionEventAnnotationsRequest(ContractRequest):
    """One nonempty atomic batch with unique target event identities."""

    annotations: list[InjectionEventAnnotationInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_unique_targets(self) -> InjectionEventAnnotationsRequest:
        targets = [annotation.target_event_uid for annotation in self.annotations]
        if len(set(targets)) != len(targets):
            raise ValueError("annotations must have unique target_event_uid values")
        return self


class InjectionEventAnnotationsResponse(ContractModel):
    """Idempotent acceptance count, including identical replays."""

    accepted: int = Field(strict=True, ge=1)


class CreatedMemoryResponse(ContractModel):
    created: MemoryUnit


class MemorySplitResponse(ContractModel):
    source: MemoryUnit
    created: list[MemoryUnit]


class SimilarMemoryResponse(ContractModel):
    created: None
    similar: list[SimilarityMemoryCard]


class LabelConflictDetail(ContractModel):
    memory_id: UUID
    label: str


class LabelConflictResponse(ContractModel):
    label_conflict: LabelConflictDetail


class DuplicateMemoryResponse(ContractModel):
    duplicate_of: SimilarityMemoryCard


class CreateMemoryConflictResponse(RootModel[LabelConflictResponse | DuplicateMemoryResponse]):
    """The two exact 409 bodies for memory creation."""


class RevisionConflictResponse(ContractModel):
    conflict: MemoryUnit


class PatchMemoryConflictResponse(RootModel[LabelConflictResponse | RevisionConflictResponse]):
    """The two exact 409 bodies for a memory patch."""


class MemoryListResponse(ContractModel):
    items: list[MemoryUnit]
    total: int
    limit: int
    offset: int


class SearchResponse(ContractModel):
    results: list[SimilarityMemoryCard]
