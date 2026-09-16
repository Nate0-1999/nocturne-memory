"""Wire contracts for the authenticated M2F retrain trigger."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict

from spine.ids import normalize_ulid


class CompactionTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_uid: Annotated[str, AfterValidator(normalize_ulid)]
    thread_id: UUID


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


__all__ = ["ReplayScoreView", "RetrainResponse"]
