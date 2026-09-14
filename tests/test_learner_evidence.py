"""Replay projection tests for M3MS room-pressure evidence."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

from spine.inject.scorer import ScorerConfig
from spine.learner.evidence import project_learning_evidence


def _row(
    event: int,
    gate: int,
    memory: int,
    *,
    shown_as: str,
    outcome: str | None,
    body: str,
    rank: int = 1,
    pin: bool = False,
) -> SimpleNamespace:
    features = {
        "sem": 0.5,
        "kw": 0.5,
        "time": 0.5,
        "proj": 0.5,
        "freq": 0.5,
        "hist": 0.5,
        "_memory": {"body": body, "pin": pin},
        "_prepare": {"model_context_tokens": 1_000, "share_tokens": 100},
    }
    return SimpleNamespace(
        event_uid=f"event-{event:02d}",
        injection_id=UUID(int=gate),
        memory_id=UUID(int=memory),
        ts=datetime(2026, 8, 24, tzinfo=UTC) + timedelta(minutes=event),
        features=features,
        score=0.5,
        scorer_version="m3ms",
        shown_as=shown_as,
        outcome=outcome,
        actor_class="human",
        principal_id="owner",
        machine_id="studio-mac",
        rank=rank,
    )


def test_room_pressure_projects_valuable_cuts_marginal_waste_and_pin_overflow() -> None:
    """A-061 projects all three room-pressure signals without changing injection truth."""
    config = ScorerConfig.from_mappings(
        version="m3ms",
        weights={
            "sem": 0.42,
            "kw": 0.16,
            "time": 0.11,
            "proj": 0.16,
            "freq": 0.08,
            "hist": 0.07,
        },
        params={
            "tau": 0.55,
            "top_k": 8,
            "near_miss_k": 3,
            "memory_context_share": 0.10,
            "half_life_time_days": 14,
            "half_life_hist_days": 7,
            "candidate_pool": 50,
        },
    )
    rows = [
        _row(1, 1, 1, shown_as="injected", outcome="kept", body="regular " * 40),
        _row(2, 1, 2, shown_as="budget_cut", outcome=None, body="valuable " * 40, rank=2),
        _row(3, 2, 2, shown_as="injected", outcome="cited", body="valuable " * 40),
        _row(4, 3, 3, shown_as="injected", outcome="kept", body="uncited " * 40),
        _row(
            5,
            4,
            4,
            shown_as="injected",
            outcome="removed:not_relevant",
            body="removed " * 40,
        ),
        _row(6, 5, 5, shown_as="pinned", outcome="kept", body="pinned-one " * 70, pin=True),
        _row(7, 5, 6, shown_as="pinned", outcome="kept", body="pinned-two " * 70, pin=True),
    ]

    evidence = project_learning_evidence(
        rows,  # type: ignore[arg-type]
        [],
        {"m3ms": config},
        passive_discount=Decimal("0.25"),
    )

    kinds = {boundary.kind for boundary in evidence.share_boundaries}
    assert "valuable_budget_cut" in kinds
    assert "marginal_uncited" in kinds
    assert "marginal_removed" in kinds
    assert "pin_overflow" in kinds
