"""Exact A-035 wire contracts for the Memory Graph and Injection Console."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from spine.contracts import MemoryFeatures, MemoryUnit
from spine.learner.contracts import ReplayScoreView


def _nonblank(value: str) -> str:
    if not value.strip() or value != value.strip():
        raise ValueError("value must be nonblank without surrounding whitespace")
    return value


type NonBlankString = Annotated[StrictStr, AfterValidator(_nonblank)]
type SignedDecimalString = Annotated[
    StrictStr,
    Field(pattern=r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"),
]
type NonNegativeDecimalString = Annotated[
    StrictStr,
    Field(pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"),
]
type NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
type PositiveInt = Annotated[StrictInt, Field(gt=0)]
type FeatureName = Literal["sem", "kw", "time", "proj", "freq", "hist"]
type ScorerParameterId = Literal[
    "scorer.tau",
    "scorer.top_k",
    "scorer.memory_context_share",
    "scorer.half_life_time_days",
    "scorer.half_life_hist_days",
    "scorer.weight.sem",
    "scorer.weight.kw",
    "scorer.weight.time",
    "scorer.weight.proj",
    "scorer.weight.freq",
    "scorer.weight.hist",
]


class M2KContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MemoryGraphQuery(M2KContract):
    principal_id: NonBlankString
    memory_ids: list[UUID] | None

    @model_validator(mode="after")
    def unique_memory_ids(self) -> MemoryGraphQuery:
        if self.memory_ids is not None and len(self.memory_ids) != len(set(self.memory_ids)):
            raise ValueError("memory_ids must be unique")
        return self


class RevisionTrailItem(M2KContract):
    rev_uid: NonBlankString
    parent_uid: NonBlankString | None
    revision: PositiveInt | None
    ts: AwareDatetime
    reason: str
    label: str
    body: str


class MemoryGraphNode(M2KContract):
    memory: MemoryUnit
    in_current_context: bool
    revisions: list[RevisionTrailItem]


class MemoryGraphEdge(M2KContract):
    kind: Literal["similarity", "lineage", "edit_trail"]
    from_memory_id: UUID
    to_memory_id: UUID
    similarity: SignedDecimalString | None = None
    edge_type: Literal["merged_from", "supersedes", "contradicts", "relates_to"] | None = None
    revision_count: PositiveInt | None = None

    @model_validator(mode="after")
    def exact_payload_for_kind(self) -> MemoryGraphEdge:
        populated = (
            self.similarity is not None,
            self.edge_type is not None,
            self.revision_count is not None,
        )
        expected = {
            "similarity": (True, False, False),
            "lineage": (False, True, False),
            "edit_trail": (False, False, True),
        }[self.kind]
        if populated != expected:
            raise ValueError("graph edge payload does not match its kind")
        return self


class MemoryGraphSnapshot(M2KContract):
    as_of: AwareDatetime
    graph_edge_sim: float
    nodes: list[MemoryGraphNode]
    edges: list[MemoryGraphEdge]
    omitted_memory_ids: list[UUID]


class ScorerConsoleQuery(M2KContract):
    principal_id: NonBlankString
    thread_id: UUID | None
    as_of: AwareDatetime | Literal["now"] = "now"


class ParameterRange(M2KContract):
    minimum: float
    maximum: float | None
    step: float
    exclusive_minimum: bool = False


class ScorerDescriptor(M2KContract):
    id: NonBlankString
    label: NonBlankString
    type: Literal["number", "integer"]
    range: ParameterRange
    default: float | int
    scope: Literal["global"] = "global"
    authority: Literal["law-bound"] = "law-bound"


class ScorerValues(M2KContract):
    tau: Annotated[StrictFloat, Field(ge=0.0, le=1.0)]
    top_k: Annotated[StrictInt, Field(ge=1, le=8)]
    memory_context_share: Annotated[StrictFloat, Field(ge=0.01, le=0.50)]
    half_life_time_days: Annotated[StrictFloat, Field(gt=0.0)]
    half_life_hist_days: Annotated[StrictFloat, Field(gt=0.0)]
    weights: dict[FeatureName, Annotated[StrictFloat, Field(ge=0.0, le=1.0)]]

    @model_validator(mode="after")
    def exact_weight_vector(self) -> ScorerValues:
        required = {"sem", "kw", "time", "proj", "freq", "hist"}
        if set(self.weights) != required:
            raise ValueError("weights must contain the exact six scorer features")
        if abs(sum(self.weights.values()) - 1.0) > 1e-9:
            raise ValueError("weights must sum to one")
        return self


class ScorerConfigurationView(M2KContract):
    version: NonBlankString
    created_at: AwareDatetime
    status: Literal["active", "proposed", "inactive"]
    values: ScorerValues
    replay: dict[str, Any] | None


class ScorerActivationView(M2KContract):
    event_uid: NonBlankString
    version: NonBlankString
    previous_version: NonBlankString
    actor_class: Literal["human", "passive"]
    machine_id: NonBlankString
    reason: Literal["human_control", "learner_proposal", "contract_migration"]
    changes: dict[str, Any]
    ts: AwareDatetime


class AccuracyPoint(M2KContract):
    version: NonBlankString
    created_at: AwareDatetime
    status: Literal["measured", "not_recorded"]
    accuracy_percent: SignedDecimalString | None
    holdout_dispositions: NonNegativeInt | None
    disagreements: NonNegativeInt | None
    weighted_dispositions: NonNegativeDecimalString | None
    weighted_wrong: NonNegativeDecimalString | None


class LearnerRunView(M2KContract):
    run_uid: NonBlankString
    trigger: Literal["manual", "background"]
    result: Literal["insufficient_data", "not_better", "proposed"]
    incumbent_version: NonBlankString
    proposal_version: NonBlankString | None
    eligible_dispositions: NonNegativeInt
    training_dispositions: NonNegativeInt
    holdout_dispositions: NonNegativeInt
    training_pairs: NonNegativeInt
    source_boundary: NonBlankString | None
    incumbent: ReplayScoreView | None
    challenger: ReplayScoreView | None
    reason: str
    ts: AwareDatetime


class LiveAgreementPoint(M2KContract):
    event_uid: NonBlankString
    ts: AwareDatetime
    scorer_version: NonBlankString
    right: NonNegativeInt
    wrong: NonNegativeInt
    weighted_right: NonNegativeDecimalString
    weighted_wrong: NonNegativeDecimalString
    weighted_agreement_percent: NonNegativeDecimalString


class LearningAnnotation(M2KContract):
    kind: Literal["activation", "force_values", "retrain"]
    event_uid: NonBlankString
    ts: AwareDatetime
    version: NonBlankString
    result: Literal["insufficient_data", "not_better", "proposed"] | None


class LearningView(M2KContract):
    eligible_dispositions: NonNegativeInt
    hygiene_excluded_dispositions: NonNegativeInt
    minimum_dispositions: PositiveInt
    remaining_to_floor: NonNegativeInt
    floor_met: bool
    share_tuning_minimum: PositiveInt
    share_tuning_remaining: NonNegativeInt
    share_tuning_active: bool
    retrain_signal_stride: PositiveInt
    evaluated_through: NonNegativeInt | None
    signals_since_last_run: NonNegativeInt
    signals_until_next_run: NonNegativeInt
    active_scorer_version: NonBlankString
    right: NonNegativeInt
    wrong: NonNegativeInt
    weighted_right: NonNegativeDecimalString
    weighted_wrong: NonNegativeDecimalString
    weighted_agreement_percent: NonNegativeDecimalString | None
    live_agreement: list[LiveAgreementPoint]
    retrain_runs: list[LearnerRunView]
    annotations: list[LearningAnnotation]


class ContributionBreakdown(M2KContract):
    sem: SignedDecimalString
    kw: SignedDecimalString
    time: SignedDecimalString
    proj: SignedDecimalString
    freq: SignedDecimalString
    hist: SignedDecimalString
    loc: SignedDecimalString | None = None
    thread: SignedDecimalString | None = None
    bias: SignedDecimalString


class CandidateScorePoint(M2KContract):
    event_uid: NonBlankString
    injection_id: UUID
    ts: AwareDatetime
    scorer_version: NonBlankString
    score: SignedDecimalString
    rank: PositiveInt
    shown_as: Literal["injected", "near_miss", "pinned", "budget_cut"]
    outcome: str | None
    features: MemoryFeatures
    contributions: ContributionBreakdown


class CandidateScoreHistory(M2KContract):
    memory_id: UUID
    label: str
    kind: str
    points: list[CandidateScorePoint]


class ScorerConsoleSnapshot(M2KContract):
    as_of: AwareDatetime
    metrics_scope: Literal["principal", "palace"] = "palace"
    scope: Literal["GLOBAL", "CURRENT"]
    thread_id: UUID | None
    descriptors: list[ScorerDescriptor]
    active_version: NonBlankString
    configurations: list[ScorerConfigurationView]
    activations: list[ScorerActivationView]
    proposed_versions: list[ScorerConfigurationView]
    accuracy: list[AccuracyPoint]
    learning: LearningView
    candidates: list[CandidateScoreHistory]


class CreateScorerConfigRequest(M2KContract):
    event_uid: NonBlankString
    base_version: NonBlankString
    values: ScorerValues
    simulation_digest: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    force: Literal[True]
    actor_class: Literal["human"]
    machine_id: NonBlankString


class ActivateScorerConfigRequest(M2KContract):
    event_uid: NonBlankString
    actor_class: Literal["human"]
    machine_id: NonBlankString


class ScorerSimulationRequest(M2KContract):
    principal_id: NonBlankString
    injection_id: UUID | None
    base_version: NonBlankString
    values: ScorerValues
    slice_parameter_id: ScorerParameterId


class ScorerComparisonRow(M2KContract):
    memory_id: UUID
    label: str
    incumbent_score: SignedDecimalString
    preview_score: SignedDecimalString
    score_delta: SignedDecimalString
    incumbent_rank: PositiveInt
    preview_rank: PositiveInt
    incumbent_selected: bool
    preview_selected: bool
    disposition: Literal["also_shown", "would_add", "would_drop", "still_out"]


class InstantSimulation(M2KContract):
    status: Literal["ready", "not_requested", "not_replayable"]
    injection_id: UUID | None
    candidates: list[ScorerComparisonRow]


class AccuracySlicePoint(M2KContract):
    value: float | int
    accuracy_percent: SignedDecimalString | None


class AccuracySlice(M2KContract):
    parameter_id: ScorerParameterId
    points: list[AccuracySlicePoint]


class ScorerSimulationResponse(M2KContract):
    simulation_digest: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    base_version: NonBlankString
    values: ScorerValues
    source_boundary: NonBlankString | None
    holdout_dispositions: NonNegativeInt
    accuracy_percent: SignedDecimalString | None
    incumbent_accuracy_percent: SignedDecimalString | None
    delta_percent: SignedDecimalString | None
    instant: InstantSimulation
    slice: AccuracySlice


class ScorerAuditionRequest(M2KContract):
    principal_id: NonBlankString
    injection_id: UUID
    proposal_version: NonBlankString


class ScorerAuditionResponse(M2KContract):
    incumbent_version: NonBlankString
    proposal_version: NonBlankString
    instant: InstantSimulation


__all__ = [
    "ActivateScorerConfigRequest",
    "CandidateScoreHistory",
    "CandidateScorePoint",
    "ContributionBreakdown",
    "CreateScorerConfigRequest",
    "LearnerRunView",
    "LearningView",
    "LiveAgreementPoint",
    "MemoryGraphQuery",
    "MemoryGraphSnapshot",
    "ScorerConfigurationView",
    "ScorerAuditionRequest",
    "ScorerAuditionResponse",
    "ScorerConsoleQuery",
    "ScorerConsoleSnapshot",
    "ScorerDescriptor",
    "ScorerSimulationRequest",
    "ScorerSimulationResponse",
    "ScorerValues",
]
