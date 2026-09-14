"""Golden tests for M2F's pure fit, hygiene, split, and binary referee."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from spine.learner.model import (
    FitSettings,
    LearningExample,
    ReplayScore,
    ShareBoundary,
    challenger_score,
    challenger_wins,
    disposition,
    fit_pairwise,
    identity_is_excluded,
    recorded_score,
    split_gates,
)

BASE = datetime(2026, 8, 3, tzinfo=UTC)
POSITIVE_ID = UUID("00000000-0000-0000-0000-000000000001")
NEGATIVE_ID = UUID("00000000-0000-0000-0000-000000000002")


def _example(
    *,
    event: int,
    gate: int,
    memory_id: UUID,
    sem: float,
    target: bool,
    shown_as: str,
    actor_weight: Decimal = Decimal(1),
    thread_feature: float | None = None,
    where_feature: float | None = None,
) -> LearningExample:
    return LearningExample(
        event_uid=f"event-{event:02d}",
        injection_id=UUID(int=gate),
        memory_id=memory_id,
        ts=BASE + timedelta(hours=gate),
        features=(sem, 0.0, 0.0, 0.0, 0.0, 0.0),
        baseline_bias=0.0,
        target_injected=target,
        actor_weight=actor_weight,
        shown_as=shown_as,  # type: ignore[arg-type]
        body_tokens=event,
        thread_feature=thread_feature,
        where_feature=where_feature,
    )


def test_disposition_and_hygiene_are_actor_classed_without_self_training() -> None:
    """A-031 is defended by verifying that disposition and hygiene are actor classed without
    self training; this prevents drift in the deterministic learner and replay scoreboard.
    """
    discount = Decimal("0.25")

    assert disposition("kept", "human", passive_discount=discount) == (
        True,
        Decimal(1),
    )
    assert disposition("removed:never", "human", passive_discount=discount) == (
        False,
        Decimal(1),
    )
    assert disposition("auto_entered", "passive", passive_discount=discount) == (
        True,
        discount,
    )
    assert disposition("mid_thread_removed", "passive", passive_discount=discount) == (
        False,
        Decimal(1),
    )
    assert disposition("mid_thread_added", "passive", passive_discount=discount) == (
        True,
        Decimal(1),
    )
    assert disposition("auto_exited", "passive", passive_discount=discount) is None
    assert disposition("removed:wrong", "human", passive_discount=discount) is None
    assert identity_is_excluded(principal_id="owner", machine_id="m2g-sop-verification")
    assert identity_is_excluded(principal_id="fixture:gate", machine_id="owner-mac")
    assert identity_is_excluded(principal_id="owner", machine_id="d1-relay")
    assert identity_is_excluded(
        principal_id="nocturne-deploy-verify-ea5a431ef134474881a7f046bb52982e",
        machine_id="nocturne-deploy",
    )
    assert identity_is_excluded(
        principal_id="owner",
        machine_id="nocturne-deploy-verification",
    )
    assert not identity_is_excluded(principal_id="owner", machine_id="studio-mac")
    assert not identity_is_excluded(principal_id="owner", machine_id="d1_relay")
    assert not identity_is_excluded(
        principal_id="nocturne_deploy_verify-run",
        machine_id="nocturne-deploy",
    )
    assert not identity_is_excluded(principal_id="owner-verify-tool", machine_id="studio-mac")
    assert not identity_is_excluded(principal_id="d1-relay", machine_id="studio-mac")
    assert not identity_is_excluded(
        principal_id="owner",
        machine_id="nocturne-deploy-verify-run",
    )


def test_time_split_keeps_whole_gates_and_uses_newest_for_holdout() -> None:
    """A-031 is defended by verifying that time split keeps whole gates and uses newest for
    holdout; this prevents drift in the deterministic learner and replay scoreboard.
    """
    examples = tuple(
        _example(
            event=index,
            gate=gate,
            memory_id=POSITIVE_ID if index % 2 else NEGATIVE_ID,
            sem=float(index % 2),
            target=bool(index % 2),
            shown_as="injected",
        )
        for index, gate in enumerate((1, 1, 2, 2, 3, 3), start=1)
    )

    training, holdout, cutoff = split_gates(examples, holdout_fraction=0.34)

    assert {item.injection_id.int for item in training} == {1}
    assert {item.injection_id.int for item in holdout} == {2, 3}
    assert cutoff == BASE + timedelta(hours=1)


def test_whole_log_fit_is_deterministic_simplex_constrained_and_shrunk() -> None:
    """A-031 is defended by verifying that whole log fit is deterministic simplex constrained
    and shrunk; this prevents drift in the deterministic learner and replay scoreboard.
    """
    examples = (
        _example(
            event=1,
            gate=1,
            memory_id=POSITIVE_ID,
            sem=1.0,
            target=True,
            shown_as="near_miss",
        ),
        _example(
            event=2,
            gate=1,
            memory_id=NEGATIVE_ID,
            sem=0.0,
            target=False,
            shown_as="injected",
        ),
    )
    settings = FitSettings(pair_margin=0.8, bias_l2=1.0)

    first = fit_pairwise(
        examples,
        incumbent_weights=(1 / 6,) * 6,
        settings=settings,
    )
    second = fit_pairwise(
        examples,
        incumbent_weights=(1 / 6,) * 6,
        settings=settings,
    )

    assert first == second
    assert sum(first.weights) == pytest.approx(1.0)
    assert all(weight >= 0.0 for weight in first.weights)
    assert first.weights[0] > 1 / 6
    assert first.bias_offsets[POSITIVE_ID] > 0.0
    assert first.bias_offsets[NEGATIVE_ID] < 0.0
    assert abs(first.bias_offsets[POSITIVE_ID]) < settings.pair_margin
    assert first.pair_count == 1


def test_fit_learns_thread_locality_from_human_pairwise_evidence() -> None:
    """A-060 lets Chrysopoeia learn the hidden thread coefficient from dispositions."""

    examples = (
        _example(
            event=1,
            gate=1,
            memory_id=POSITIVE_ID,
            sem=0.5,
            target=True,
            shown_as="near_miss",
            thread_feature=1.0,
        ),
        _example(
            event=2,
            gate=1,
            memory_id=NEGATIVE_ID,
            sem=0.5,
            target=False,
            shown_as="injected",
            thread_feature=0.0,
        ),
    )

    fit = fit_pairwise(
        examples,
        incumbent_weights=(1 / 6,) * 6,
        incumbent_thread_weight=0.08,
        settings=FitSettings(pair_margin=0.4, bias_l2=100.0),
    )

    assert fit.thread_weight > 0.08
    assert fit.thread_weight < 1.0


def test_fit_learns_where_locality_from_human_pairwise_evidence() -> None:
    """A-063 registers WHERE with the one learner instead of freezing a hidden coefficient."""

    examples = (
        _example(
            event=1,
            gate=1,
            memory_id=POSITIVE_ID,
            sem=0.5,
            target=True,
            shown_as="near_miss",
            where_feature=1.0,
        ),
        _example(
            event=2,
            gate=1,
            memory_id=NEGATIVE_ID,
            sem=0.5,
            target=False,
            shown_as="injected",
            where_feature=0.0,
        ),
    )

    fit = fit_pairwise(
        examples,
        incumbent_weights=(1 / 6,) * 6,
        incumbent_where_weight=0.04,
        settings=FitSettings(pair_margin=0.4, bias_l2=100.0),
    )

    assert fit.where_weight > 0.04
    assert fit.where_weight < 1.0


def test_share_and_line_stay_fixed_until_the_authentic_feedback_floor() -> None:
    """SPEC D.2 133 keeps the new controls fixed, then joins them to the one learner."""

    examples = (
        _example(
            event=1,
            gate=1,
            memory_id=POSITIVE_ID,
            sem=0.9,
            target=True,
            shown_as="near_miss",
        ),
        _example(
            event=2,
            gate=1,
            memory_id=NEGATIVE_ID,
            sem=0.1,
            target=False,
            shown_as="injected",
        ),
    )
    boundary = ShareBoundary(
        event_uid="share-up",
        injection_id=UUID(int=1),
        required_share=0.14,
        target_at_least=True,
        actor_weight=Decimal(1),
        kind="valuable_budget_cut",
    )
    inputs = {
        "examples": examples,
        "incumbent_weights": (1 / 6,) * 6,
        "incumbent_tau": 0.55,
        "incumbent_memory_context_share": 0.10,
        "share_boundaries": (boundary,),
        "settings": FitSettings(pair_margin=0.4, bias_l2=10.0),
    }

    below_floor = fit_pairwise(**inputs, tune_share_and_tau=False)
    at_floor = fit_pairwise(**inputs, tune_share_and_tau=True)

    assert not below_floor.share_tau_active
    assert below_floor.tau == 0.55
    assert below_floor.memory_context_share == 0.10
    assert at_floor.share_tau_active
    assert at_floor.tau != 0.55
    assert at_floor.memory_context_share == 0.14


def test_share_replay_counts_room_up_and_room_down_triggers() -> None:
    """A-061 counts both insufficient room and wasted room in the same replay."""
    boundaries = (
        ShareBoundary(
            event_uid="up",
            injection_id=UUID(int=1),
            required_share=0.14,
            target_at_least=True,
            actor_weight=Decimal(1),
            kind="pin_overflow",
        ),
        ShareBoundary(
            event_uid="down",
            injection_id=UUID(int=2),
            required_share=0.18,
            target_at_least=False,
            actor_weight=Decimal("0.25"),
            kind="marginal_uncited",
        ),
    )

    tight = recorded_score((), share_boundaries=boundaries, memory_context_share=0.10)
    roomy = recorded_score((), share_boundaries=boundaries, memory_context_share=0.20)

    assert tight.share_disagreements == 1
    assert tight.weighted_share_disagreements == Decimal(1)
    assert roomy.share_disagreements == 1
    assert roomy.weighted_share_disagreements == Decimal("0.25")


def test_binary_replay_counts_each_override_and_applies_passive_discount() -> None:
    """A-031 is defended by verifying that binary replay counts each override and applies
    passive discount; this prevents drift in the deterministic learner and replay
    scoreboard.
    """
    examples = (
        _example(
            event=3,
            gate=2,
            memory_id=POSITIVE_ID,
            sem=1.0,
            target=True,
            shown_as="near_miss",
        ),
        _example(
            event=5,
            gate=2,
            memory_id=NEGATIVE_ID,
            sem=0.0,
            target=False,
            shown_as="injected",
            actor_weight=Decimal("0.25"),
        ),
    )

    incumbent = recorded_score(examples)
    challenger = challenger_score(
        examples,
        weights=(1.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        bias_offsets={},
        tau=0.55,
    )

    assert incumbent.disagreements == 2
    assert incumbent.weighted_disagreements == Decimal("1.25")
    assert incumbent.injected_tokens == 5
    assert challenger.disagreements == 0
    assert challenger.weighted_disagreements == 0
    assert challenger.injected_tokens == 3


def test_replay_winner_requires_margin_except_for_exact_cheaper_tie() -> None:
    """A-031 is defended by verifying that replay winner requires margin except for exact
    cheaper tie; this prevents drift in the deterministic learner and replay scoreboard.
    """
    incumbent = ReplayScore(
        disagreements=4,
        weighted_disagreements=Decimal("4"),
        injected_tokens=100,
    )

    assert challenger_wins(
        incumbent,
        ReplayScore(
            disagreements=3,
            weighted_disagreements=Decimal("3"),
            injected_tokens=120,
        ),
        margin=Decimal("1"),
    )
    assert not challenger_wins(
        incumbent,
        ReplayScore(
            disagreements=3,
            weighted_disagreements=Decimal("3.25"),
            injected_tokens=80,
        ),
        margin=Decimal("1"),
    )
    assert challenger_wins(
        incumbent,
        ReplayScore(
            disagreements=4,
            weighted_disagreements=Decimal("4"),
            injected_tokens=99,
        ),
        margin=Decimal("1"),
    )


def test_replay_tie_prefers_smaller_share_then_higher_tau() -> None:
    """SPEC D.2 133 makes room and line part of the replay's cheaper-at-tie law."""

    score = ReplayScore(
        disagreements=1,
        weighted_disagreements=Decimal("1.0"),
        injected_tokens=100,
    )

    assert challenger_wins(
        score,
        score,
        margin=Decimal("0.05"),
        incumbent_memory_context_share=0.20,
        challenger_memory_context_share=0.10,
        incumbent_tau=0.55,
        challenger_tau=0.55,
    )
    assert challenger_wins(
        score,
        score,
        margin=Decimal("0.05"),
        incumbent_memory_context_share=0.10,
        challenger_memory_context_share=0.10,
        incumbent_tau=0.55,
        challenger_tau=0.60,
    )
    assert not challenger_wins(
        score,
        score,
        margin=Decimal("0.05"),
        incumbent_memory_context_share=0.10,
        challenger_memory_context_share=0.20,
        incumbent_tau=0.55,
        challenger_tau=0.60,
    )
