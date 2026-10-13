"""Hand-computed golden cases for the pure SPEC C.3 scorer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from spine.inject.scorer import (
    ScorerConfig,
    ScorerParams,
    ScorerWeights,
    ScoringCandidate,
    score_and_select,
)

SNAPSHOT = datetime(2026, 7, 19, 12, tzinfo=UTC)
WEIGHTS = ScorerWeights(sem=0.42, kw=0.16, time=0.11, proj=0.16, freq=0.08, hist=0.07)


def _config(**changes: Any) -> ScorerConfig:
    values: dict[str, Any] = {
        "tau": 0.55,
        "near_miss_k": 3,
        "memory_context_share": 0.05,
        "legacy_budget_tokens": 3000,
        "half_life_time_days": 14,
        "half_life_hist_days": 7,
        "candidate_pool": 50,
    }
    if "budget_tokens" in changes:
        changes["legacy_budget_tokens"] = changes.pop("budget_tokens")
    changes.pop("budget_pct", None)
    values.update(changes)
    return ScorerConfig(version="v0", weights=WEIGHTS, params=ScorerParams(**values))


def _candidate(
    number: int,
    *,
    label: str = "memory",
    body: str = "tiny",
    kind: str = "fact",
    keywords: tuple[str, ...] = (),
    embedding: tuple[float, ...] = (1.0, 0.0),
    project_key: str | None = None,
    origin_thread_id: UUID | None = None,
    origin_path: str | None = None,
    origin_location: str | None = None,
    pin: bool = False,
    updated_at: datetime = SNAPSHOT,
    last_human_edit_at: datetime | None = None,
    citations: int = 0,
    bias: float = 0.0,
) -> ScoringCandidate:
    return ScoringCandidate(
        memory_id=UUID(int=number),
        label=label,
        body=body,
        kind=kind,
        keywords=keywords,
        embedding=embedding,
        project_key=project_key,
        origin_thread_id=origin_thread_id,
        origin_path=origin_path,
        origin_location=origin_location,
        pin=pin,
        updated_at=updated_at,
        last_human_edit_at=last_human_edit_at,
        stats={"citations": citations},
        bias=bias,
        revision=number,
    )


def test_golden_six_feature_score_uses_enacted_tokenizer_and_snapshot_clock() -> None:
    # Hand calculation:
    # sem=.6; kw=3/3=1; time=2^(-14/14)=.5; proj=1;
    # freq=5/10=.5; hist=2^(-7/7)=.5.
    # score=.42*.6 + .16 + .11*.5 + .16 + .08*.5 + .07*.5 + .03 = .732.
    """A-007 is defended by verifying that golden six feature score uses enacted tokenizer and
    snapshot clock; this prevents drift in the deterministic scorer contract.
    """
    candidate = _candidate(
        1,
        label="Café roadmap",
        body="one two",
        keywords=("NAÏVE2", "road-and-more"),
        embedding=(0.6, 0.8),
        project_key="atlas",
        updated_at=SNAPSHOT - timedelta(days=14),
        last_human_edit_at=SNAPSHOT - timedelta(days=7),
        citations=5,
        bias=0.03,
    )

    result = score_and_select(
        prompt="The CAFÉ_and naïve2 from ROAD",
        query_embedding=(1.0, 0.0),
        snapshot_ts=SNAPSHOT,
        thread_project_key="atlas",
        pinned_candidates=(),
        regular_candidates=(candidate,),
        model_context_tokens=10_000,
        config=_config(),
    )

    assert len(result.injected) == 1
    scored = result.injected[0]
    assert scored.features.as_dict() == pytest.approx(
        {"sem": 0.6, "kw": 1.0, "time": 0.5, "proj": 1.0, "freq": 0.5, "hist": 0.5}
    )
    assert scored.score == pytest.approx(0.732)
    assert scored.rank == 1
    assert scored.token_cost == 2


def test_thread_feature_rewards_only_a_known_matching_birthplace() -> None:
    """A-060 makes conversation locality exact and excludes missing birthplaces."""

    current_thread = UUID(int=501)
    candidates = (
        _candidate(1, origin_thread_id=current_thread),
        _candidate(2, origin_thread_id=UUID(int=502)),
        _candidate(3),
    )
    result = score_and_select(
        prompt="the and",
        query_embedding=(1.0, 0.0),
        snapshot_ts=SNAPSHOT,
        thread_project_key="atlas",
        thread_id=current_thread,
        pinned_candidates=(),
        regular_candidates=candidates,
        model_context_tokens=10_000,
        config=_config(tau=0.0, thread_weight=0.08),
    )

    by_id = {item.candidate.memory_id.int: item for item in result.injected}
    assert by_id[1].features.thread == 1.0
    assert by_id[2].features.thread == 0.0
    assert by_id[3].features.thread is None
    assert by_id[1].score == pytest.approx((1.0 - 0.08) * 0.61 + 0.08)
    assert by_id[2].score == pytest.approx((1.0 - 0.08) * 0.61)
    assert by_id[3].score == pytest.approx(0.61)


def test_zero_thread_weight_preserves_the_previous_score_exactly() -> None:
    """A-060 leaves the v0 golden score bit-for-bit unchanged at coefficient zero."""

    candidate = _candidate(1, origin_thread_id=UUID(int=601))
    result = score_and_select(
        prompt="the and",
        query_embedding=(1.0, 0.0),
        snapshot_ts=SNAPSHOT,
        thread_project_key="atlas",
        thread_id=UUID(int=601),
        pinned_candidates=(),
        regular_candidates=(candidate,),
        model_context_tokens=10_000,
        config=_config(tau=0.0, thread_weight=0.0),
    )

    assert result.injected[0].score == pytest.approx(0.61)


def test_where_feature_orders_same_ancestor_sibling_unrelated_and_missing() -> None:
    """A-063 makes folder proximity ordered, explainable, and absent for legacy rows."""

    candidates = (
        _candidate(1, origin_location="/work/atlas/api"),
        _candidate(2, origin_location="/work/atlas"),
        _candidate(3, origin_location="/work/atlas/web"),
        _candidate(4, origin_location="/elsewhere"),
        _candidate(5),
    )
    result = score_and_select(
        prompt="the and",
        query_embedding=(1.0, 0.0),
        snapshot_ts=SNAPSHOT,
        thread_project_key="/work/atlas",
        current_location="/work/atlas/api",
        pinned_candidates=(),
        regular_candidates=candidates,
        model_context_tokens=10_000,
        config=_config(tau=0.0, where_weight=0.04),
    )

    by_id = {item.candidate.memory_id.int: item for item in result.injected}
    assert by_id[1].features.where == 1.0
    assert 0.75 < (by_id[2].features.where or 0.0) < 1.0
    assert by_id[3].features.where == 0.5
    assert by_id[4].features.where == 0.0
    assert by_id[5].features.where is None
    assert by_id[1].score > by_id[2].score > by_id[3].score > by_id[4].score
    assert by_id[5].score == pytest.approx(0.61)


def test_golden_tau_is_inclusive_and_negative_cosine_clamps_to_zero() -> None:
    # Shared subtotal: sem=1 -> .42, time=1 -> .11, global project=.5 ->
    # .08, for .61. Bias -.06 lands exactly on tau; -.061 lands at .549.
    """A-007 is defended by verifying that golden tau is inclusive and negative cosine clamps
    to zero; this prevents drift in the deterministic scorer contract.
    """
    at_tau = _candidate(
        1,
        embedding=(1.0, 0.0),
        project_key=None,
        bias=-0.06,
    )
    below_tau = _candidate(
        2,
        embedding=(1.0, 0.0),
        project_key=None,
        bias=-0.061,
    )
    negative = _candidate(
        3,
        embedding=(-1.0, 0.0),
        project_key=None,
    )

    result = score_and_select(
        prompt="the and",
        query_embedding=(1.0, 0.0),
        snapshot_ts=SNAPSHOT,
        thread_project_key="atlas",
        pinned_candidates=(),
        regular_candidates=(negative, below_tau, at_tau),
        model_context_tokens=10_000,
        config=_config(),
    )

    assert [item.candidate.memory_id.int for item in result.injected] == [1]
    assert result.injected[0].score == pytest.approx(0.55)
    assert [item.candidate.memory_id.int for item in result.near_misses] == [2, 3]
    assert [item.score for item in result.near_misses] == pytest.approx([0.549, 0.19])
    assert result.near_misses[1].features.sem == 0.0


def test_golden_selection_preserves_pin_score_order_ranks_and_greedy_skips() -> None:
    # With only stopwords in the prompt, every regular has the same .61 base:
    # .42*1 sem + .11*1 time + .16*.5 global project = .61.
    # Biases therefore make regular scores .90, .80, .70, .60, .50.
    """A-007 and D.2 101(3) require deterministic greedy skips and an exact budget-cut band;
    this prevents replay instrumentation from changing scorer behavior.
    """
    pins = (
        _candidate(2, pin=True, body="one two"),
        _candidate(1, pin=True, body="pin"),
    )
    regular = (
        _candidate(14, bias=-0.11),
        _candidate(13, bias=-0.01),
        _candidate(12, body="one two", bias=0.09),
        _candidate(11, body="one two three", kind="pinned", bias=0.19),
        _candidate(10, body="one two three", bias=0.29),
    )

    result = score_and_select(
        prompt="the and from",
        query_embedding=(1.0, 0.0),
        snapshot_ts=SNAPSHOT,
        thread_project_key="atlas",
        pinned_candidates=pins,
        regular_candidates=regular,
        model_context_tokens=160,
        config=_config(budget_tokens=8, candidate_pool=5),
    )

    # floor(.05*160)=8 remains wholly available to regular memories. Pins are
    # forced beside that ceiling; ranks 3, 4 and 5 fill the eight-token share.
    assert result.pin_token_cost == 3
    assert result.regular_budget == 8
    assert result.regular_token_cost == 8
    assert result.total_token_cost == 11
    assert result.pinned_overflow_tokens == 3
    assert [item.candidate.memory_id.int for item in result.injected] == [1, 2, 10, 11, 12]
    assert [item.rank for item in result.injected] == [1, 2, 3, 4, 5]
    assert [item.candidate.memory_id.int for item in result.near_misses] == [13, 14]
    assert [item.rank for item in result.near_misses] == [6, 7]
    assert [item.candidate.memory_id.int for item in result.budget_cuts] == [13]


def test_threshold_and_share_inject_more_than_eight_without_a_count_cap() -> None:
    """SPEC ADR-005 / rules-ledger r3-3: every passing memory fits until room is spent."""
    result = score_and_select(
        prompt="the", query_embedding=(1.0, 0.0), snapshot_ts=SNAPSHOT,
        thread_project_key=None, pinned_candidates=(),
        regular_candidates=tuple(_candidate(i) for i in range(1, 21)),
        model_context_tokens=300, config=_config(),
    )
    assert [item.candidate.memory_id.int for item in result.injected] == list(range(1, 16))
    assert result.regular_token_cost == 15
    assert len(result.budget_cuts) == 5
    assert not hasattr(_config().params, "top_k")


def test_golden_pins_can_exceed_budget_and_bypass_a_below_tau_score() -> None:
    # The pin has sem=0 (negative cosine clamps), kw=0, time=1 (future age
    # clamps), proj=.5, freq=1, hist=1. Its score is .11+.08+.08+.07=.34.
    """A-007 is defended by verifying that golden pins can exceed budget and bypass a below tau
    score; this prevents drift in the deterministic scorer contract.
    """
    pin = _candidate(
        1,
        body="one two three",
        embedding=(-1.0, 0.0),
        pin=True,
        updated_at=SNAPSHOT + timedelta(days=1),
        last_human_edit_at=SNAPSHOT + timedelta(days=1),
        citations=99,
    )
    regular = _candidate(2, body="tiny")

    result = score_and_select(
        prompt="the",
        query_embedding=(1.0, 0.0),
        snapshot_ts=SNAPSHOT,
        thread_project_key=None,
        pinned_candidates=(pin,),
        regular_candidates=(regular,),
        model_context_tokens=40,
        config=_config(budget_tokens=2),
    )

    assert result.pin_token_cost == 3
    assert result.regular_budget == 2
    assert result.regular_token_cost == 1
    assert result.pinned_overflow_tokens == 2
    assert [item.candidate.memory_id.int for item in result.injected] == [1, 2]
    assert result.injected[0].score == pytest.approx(0.34)
    assert result.injected[0].features.as_dict() == pytest.approx(
        {"sem": 0.0, "kw": 0.0, "time": 1.0, "proj": 0.5, "freq": 1.0, "hist": 1.0}
    )
    assert result.near_misses == ()


def test_confirmed_lock_bypasses_threshold_inside_regular_share() -> None:
    """SPEC D.2 101 keeps confirmed locks forced within regular memory accounting."""
    locked = _candidate(2, body="one two three", embedding=(-1.0, 0.0))
    ordinary = _candidate(1, body="one")

    result = score_and_select(
        prompt="the",
        query_embedding=(1.0, 0.0),
        snapshot_ts=SNAPSHOT,
        thread_project_key=None,
        pinned_candidates=(),
        regular_candidates=(ordinary, locked),
        locked_memory_ids=frozenset({locked.memory_id}),
        model_context_tokens=80,
        config=_config(budget_tokens=4),
    )

    assert [item.candidate.memory_id for item in result.injected] == [
        locked.memory_id,
        ordinary.memory_id,
    ]
    assert result.injected[0].score < 0.55
    assert result.injected[0].rank == 1
    assert result.pin_token_cost == 0
    assert result.regular_budget == 4
    assert result.regular_token_cost == 4
    assert result.near_misses == ()


def test_confirmed_pin_is_already_forced_and_does_not_require_regular_membership() -> None:
    """A-007 is defended by verifying that confirmed pin is already forced and does not require
    regular membership; this prevents drift in the deterministic scorer contract.
    """
    pinned_and_confirmed = _candidate(7, body="one two", pin=True)

    result = score_and_select(
        prompt="the",
        query_embedding=(1.0, 0.0),
        snapshot_ts=SNAPSHOT,
        thread_project_key=None,
        pinned_candidates=(pinned_and_confirmed,),
        regular_candidates=(),
        locked_memory_ids=frozenset({pinned_and_confirmed.memory_id}),
        model_context_tokens=80,
        config=_config(budget_tokens=4),
    )

    assert [item.candidate.memory_id for item in result.injected] == [
        pinned_and_confirmed.memory_id
    ]
    assert result.injected[0].rank == 1
    assert result.near_misses == ()


def test_every_supplied_pool_candidate_is_scored_before_threshold_and_budget_selection() -> None:
    # Candidate retrieval owns the vector/FTS bounds. Even with the historical
    # candidate_pool config set to one, the scorer must rank every member of
    # the already-bounded union supplied by the service.
    """A-007 is defended by verifying that every supplied pool candidate is scored before
    threshold and budget selection; this prevents drift in the deterministic scorer
    contract.
    """
    highest_but_over_budget = _candidate(
        30,
        label="Needle",
        body="one two three",
        embedding=(-1.0, 0.0),
        bias=0.7,
    )
    selected = _candidate(20, body="x", embedding=(1.0, 0.0))
    below_threshold = _candidate(10, body="x", embedding=(0.8, 0.6))

    result = score_and_select(
        prompt="needle",
        query_embedding=(1.0, 0.0),
        snapshot_ts=SNAPSHOT,
        thread_project_key=None,
        pinned_candidates=(),
        regular_candidates=(below_threshold, selected, highest_but_over_budget),
        model_context_tokens=40,
        config=_config(candidate_pool=1, budget_tokens=2),
    )

    assert result.regular_budget == 2
    assert [item.candidate.memory_id.int for item in result.injected] == [20]
    assert [item.rank for item in result.injected] == [2]
    assert [item.candidate.memory_id.int for item in result.near_misses] == [30, 10]
    assert [item.rank for item in result.near_misses] == [1, 3]

    ranked = sorted(
        (*result.injected, *result.near_misses),
        key=lambda item: item.rank,
    )
    assert [item.score for item in ranked] == pytest.approx([1.05, 0.61, 0.526])
    assert ranked[0].features.as_dict() == pytest.approx(
        {"sem": 0.0, "kw": 1.0, "time": 1.0, "proj": 0.5, "freq": 0.0, "hist": 0.0}
    )


def test_postgres_real_quantization_precedes_score_tie_breaking() -> None:
    """A-007 is defended by verifying that postgres real quantization precedes score tie
    breaking; this prevents drift in the deterministic scorer contract.
    """
    lower_id = _candidate(1, bias=0.0)
    higher_raw_score = _candidate(2, bias=1e-9)

    result = score_and_select(
        prompt="the",
        query_embedding=(1.0, 0.0),
        snapshot_ts=SNAPSHOT,
        thread_project_key=None,
        pinned_candidates=(),
        regular_candidates=(higher_raw_score, lower_id),
        model_context_tokens=10_000,
        config=_config(tau=0.0),
    )

    assert result.injected[0].score == result.injected[1].score
    assert [item.candidate.memory_id.int for item in result.injected] == [1, 2]


def test_percentage_budget_saturates_for_an_arbitrarily_large_context_integer() -> None:
    """A-007 is defended by verifying that percentage budget saturates for an arbitrarily large
    context integer; this prevents drift in the deterministic scorer contract.
    """
    candidate = _candidate(1)

    result = score_and_select(
        prompt="the",
        query_embedding=(1.0, 0.0),
        snapshot_ts=SNAPSHOT,
        thread_project_key=None,
        pinned_candidates=(),
        regular_candidates=(candidate,),
        model_context_tokens=10**400,
        config=_config(),
    )

    assert result.regular_budget == 3000
    assert [item.candidate.memory_id.int for item in result.injected] == [1]


def test_config_json_boundary_uses_only_the_scoring_fields() -> None:
    """A-007 is defended by verifying that config json boundary uses only the scoring fields;
    this prevents drift in the deterministic scorer contract.
    """
    config = ScorerConfig.from_mappings(
        version="v0",
        weights={"sem": 0.42, "kw": 0.16, "time": 0.11, "proj": 0.16, "freq": 0.08, "hist": 0.07},
        params={
            "tau": 0.55,
            "top_k": 8,
            "near_miss_k": 3,
            "budget_tokens": 3000,
            "budget_pct": 0.05,
            "half_life_time_days": 14,
            "half_life_hist_days": 7,
            "candidate_pool": 50,
            "never_bias_step": -0.15,
            "quarantine_kills": 3,
        },
    )

    assert config == _config()


def test_r16_location_distance_and_null_renormalization_follow_the_feet() -> None:
    """A-058 activates same-directory affinity without penalizing missing location."""

    candidate = _candidate(1, project_key="atlas", origin_path="src/feature")
    config = _config(location_weight=0.08, half_life_location_hops=2, tau=0.0)

    def scored(location_path: str | None, project_key: str = "atlas"):
        return score_and_select(
            prompt="the",
            query_embedding=(1.0, 0.0),
            snapshot_ts=SNAPSHOT,
            thread_project_key=project_key,
            location_path=location_path,
            pinned_candidates=(),
            regular_candidates=(candidate,),
            model_context_tokens=10_000,
            config=config,
        ).injected[0]

    same = scored("src/feature")
    two_hops = scored("src/other")
    missing = scored(None)
    other_workspace = scored("src/feature", project_key="other")

    assert same.features.loc == 1.0
    assert two_hops.features.loc == pytest.approx(0.5)
    assert missing.features.loc is None
    assert other_workspace.features.loc == 0.0
    assert same.score == pytest.approx(0.7148)
    assert two_hops.score == pytest.approx(0.6748)
    assert missing.score == pytest.approx(0.69)


def test_active_learner_version_adds_immutable_offset_to_online_head_bias() -> None:
    """A-007 is defended by verifying that active learner version adds immutable offset to
    online head bias; this prevents drift in the deterministic scorer contract.
    """
    memory_id = UUID(int=1)
    config = ScorerConfig.from_mappings(
        version="m2f-proposal",
        weights={"sem": 0.42, "kw": 0.16, "time": 0.11, "proj": 0.16, "freq": 0.08, "hist": 0.07},
        params={
            "tau": 0.55,
            "top_k": 8,
            "near_miss_k": 3,
            "budget_tokens": 3000,
            "budget_pct": 0.05,
            "half_life_time_days": 14,
            "half_life_hist_days": 7,
            "candidate_pool": 50,
            "_learner": {"bias_offsets": {str(memory_id): 0.2}},
        },
    )
    candidate = _candidate(1, bias=-0.1)

    result = score_and_select(
        prompt="the",
        query_embedding=(1.0, 0.0),
        snapshot_ts=SNAPSHOT,
        thread_project_key=None,
        pinned_candidates=(),
        regular_candidates=(candidate,),
        model_context_tokens=10_000,
        config=config,
    )

    # Base .61 + online safety bias -.10 + version offset +.20 = .71.
    assert result.injected[0].score == pytest.approx(0.71)
    assert config.bias_offset(memory_id) == 0.2
