"""Wire contracts for the authenticated M2F retrain trigger."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from spine.ids import normalize_ulid


class CompactionPolicy(BaseModel):
    id: str
    parent: str | None
    instructions: str
    memory_size: int = Field(ge=16, le=128)


class CompactionFact(BaseModel):
    body: str
    memory_id: UUID


class CompactionRecord(BaseModel):
    policy: CompactionPolicy
    source: str
    machine_id: str
    facts: list[CompactionFact]


class CompactionTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_uid: Annotated[str, AfterValidator(normalize_ulid)]
    thread_id: UUID
    record: CompactionRecord | None = None


class CompactionBacktest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    memory_size: int | None = Field(default=None, ge=16, le=128)
    preview: bool = False


class ReplayScoreView(BaseModel):
    disagreements: int
    weighted_disagreements: str
    injected_tokens: int
    share_disagreements: int = 0
    weighted_share_disagreements: str = "0"


class RetrainResponse(BaseModel):
    status: Literal["insufficient_data", "not_better", "proposed"]
    incumbent_version: str
    proposal_version: str | None
    eligible_dispositions: int
    training_dispositions: int
    holdout_dispositions: int
    training_pairs: int
    incumbent: ReplayScoreView | None
    challenger: ReplayScoreView | None
    reason: str


class RetrainJobView(BaseModel):
    job_id: str
    status: Literal["pending", "completed", "failed"]
    result: RetrainResponse | None = None
    error: str | None = None


__all__ = ["ReplayScoreView", "RetrainResponse", "RetrainJobView"]
