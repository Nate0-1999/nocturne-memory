"""Closed contracts for M3CU Palace Health Reports and curator activity."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from spine.contracts import ContractModel, MemoryKind

FindingKind = Literal["duplicate", "contradiction", "stale", "slop", "keyword"]
CuratorActionName = Literal[
    "keep", "merge", "contradict", "supersede", "retire", "keyword_repair", "split"
]


class HealthFinding(ContractModel):
    ordinal: int = Field(ge=0)
    kind: FindingKind
    memory_ids: list[UUID] = Field(min_length=1)
    evidence: dict[str, Any]
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class PalaceHealthReport(ContractModel):
    format: Literal["nocturne.palace-health"] = "nocturne.palace-health"
    version: Literal["1"] = "1"
    principal_id: str = Field(min_length=1)
    as_of: datetime
    corpus_revision: str
    active_units: int = Field(ge=0)
    clusters: list[list[UUID]]
    findings: list[HealthFinding]
    keyword_coverage_percent: str
    stats_delta: dict[str, int]


class CuratorSplitChild(ContractModel):
    label: str = Field(min_length=1, max_length=64)
    body: str = Field(min_length=1)
    kind: MemoryKind
    keywords: list[str] = Field(min_length=2, max_length=5)


class CuratorVerdictDraft(ContractModel):
    action: CuratorActionName
    rationale: str = Field(min_length=1, max_length=2000)
    label: str | None = Field(default=None, max_length=64)
    body: str | None = None
    keywords: list[str] | None = None
    children: list[CuratorSplitChild] | None = None

    @model_validator(mode="after")
    def require_action_payload(self) -> CuratorVerdictDraft:
        replacement = self.action in {"merge", "supersede"}
        if replacement and (self.label is None or self.body is None or self.keywords is None):
            raise ValueError("merge and supersede verdicts require label, body, and keywords")
        if self.action == "keyword_repair" and self.keywords is None:
            raise ValueError("keyword_repair requires keywords")
        if self.action == "split" and (self.children is None or len(self.children) < 2):
            raise ValueError("split requires at least two semantic children")
        if self.action not in {"merge", "supersede"} and (
            self.label is not None or self.body is not None
        ):
            raise ValueError("only replacement verdicts carry label and body")
        if self.action != "split" and self.children is not None:
            raise ValueError("only split verdicts carry children")
        return self


class CuratorRunRequest(ContractModel):
    principal_id: str = Field(min_length=1)
    machine_id: str = Field(min_length=1)


ProgressPhase = Literal[
    "run.started", "finding.started", "finding.completed", "run.completed", "run.failed"
]


class CuratorProgressEvent(ContractModel):
    event_id: int
    run_uid: str
    phase: ProgressPhase
    memory_ids: list[UUID]
    finding_uid: str | None
    action: str | None
    ts: datetime


class CuratorProgress(ContractModel):
    events: list[CuratorProgressEvent]
    cursor: int


class CuratorRunReceipt(ContractModel):
    run_uid: str
    principal_id: str
    trigger: Literal["writes", "manual", "injection_pressure", "cron"]
    status: Literal["completed", "failed"]
    admitted_writes_snapshot: int = Field(ge=0)
    pressure_snapshot: int = Field(ge=0)
    verdict_count: int = Field(ge=0)
    queued_count: int = Field(ge=0)
    executed_count: int = Field(ge=0)
    report: PalaceHealthReport
    error: str | None
    completed_at: datetime


class CuratorActivity(ContractModel):
    principal_id: str
    admitted_writes: int = Field(ge=0)
    last_run_writes: int = Field(ge=0)
    pressure_events: int = Field(ge=0)
    last_run_pressure: int = Field(ge=0)
    trigger_every: int = Field(gt=0)
    pressure_trigger_every: int = Field(gt=0)
    writes_until_run: int = Field(ge=0)
    pressure_until_run: int = Field(ge=0)
    latest_run: CuratorRunReceipt | None
    pending_cards: int = Field(ge=0)


__all__ = [
    "CuratorActionName",
    "CuratorActivity",
    "CuratorRunReceipt",
    "CuratorRunRequest",
    "CuratorSplitChild",
    "CuratorVerdictDraft",
    "FindingKind",
    "HealthFinding",
    "PalaceHealthReport",
]
