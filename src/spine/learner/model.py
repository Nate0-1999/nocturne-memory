"""Pure deterministic fit and replay math for the M2F Chrysopoeia learner."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from spine.inject.axes import AxisNomination, apply_axes
from spine.inject.scorer import project_simplex as _project_simplex

FEATURE_NAMES = ("sem", "kw", "time", "proj", "freq", "hist")
EXPLICIT_POSITIVE_OUTCOMES = frozenset({"added_back", "cited", "mid_thread_added"})
NEGATIVE_OUTCOMES = frozenset({"removed:not_relevant", "removed:never", "mid_thread_removed"})
PASSIVE_POSITIVE_OUTCOMES = frozenset({"kept", "auto_entered"})
MIN_MEMORY_CONTEXT_SHARE = 0.01
MAX_MEMORY_CONTEXT_SHARE = 0.50


@dataclass(frozen=True, slots=True, kw_only=True)
class LearningExample:
    """One hygiene-passing disposition with its frozen replay inputs."""

    event_uid: str
    injection_id: UUID
    memory_id: UUID
    ts: datetime
    features: tuple[float, float, float, float, float, float]
    baseline_bias: float
    target_injected: bool
    actor_weight: Decimal
    shown_as: Literal["injected", "near_miss", "pinned", "budget_cut"]
    body_tokens: int
    location_feature: float | None = None
    location_weight: float = 0.0
    thread_feature: float | None = None
    thread_id: UUID | None = None
    where_feature: float | None = None
    where_weight: float = 0.0
    project_key: str | None = None

    @property
    def recorded_injected(self) -> bool:
        return self.shown_as not in {"near_miss", "budget_cut"}


@dataclass(frozen=True, slots=True, kw_only=True)
class ShareBoundary:
    """One replay-visible reason for the memory room to grow or shrink."""

    event_uid: str
    injection_id: UUID
    required_share: float
    target_at_least: bool
    actor_weight: Decimal
    kind: Literal["valuable_budget_cut", "marginal_uncited", "marginal_removed", "pin_overflow"]


@dataclass(frozen=True, slots=True, kw_only=True)
class FitSettings:
    pair_margin: float
    bias_l2: float


@dataclass(frozen=True, slots=True, kw_only=True)
class FitResult:
    weights: tuple[float, float, float, float, float, float]
    thread_weight: float
    where_weight: float
    tau: float
    memory_context_share: float
    share_tau_active: bool
    bias_offsets: Mapping[UUID, float]
    pair_count: int
    iterations: int
    objective: float
    project_offsets: Mapping[str, Mapping[str, float]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplayScore:
    disagreements: int
    weighted_disagreements: Decimal
    injected_tokens: int
    share_disagreements: int = 0
    weighted_share_disagreements: Decimal = Decimal(0)


def disposition(
    outcome: str | None,
    actor_class: str,
    *,
    passive_discount: Decimal,
) -> tuple[bool, Decimal] | None:
    """Map one stored outcome to A-031's binary label and actor weight."""

    if outcome in EXPLICIT_POSITIVE_OUTCOMES:
        return True, Decimal(1)
    if outcome in NEGATIVE_OUTCOMES:
        return False, Decimal(1)
    if actor_class == "human":
        if outcome == "kept":
            return True, Decimal(1)
        return None
    if actor_class == "passive" and outcome in PASSIVE_POSITIVE_OUTCOMES:
        return True, passive_discount
    return None


def identity_is_excluded(*, principal_id: str, machine_id: str) -> bool:
    """Recognize the enacted fixture/test/verification identity classes."""

    literal_principal = principal_id.strip().lower()
    literal_machine = machine_id.strip().lower()
    return (
        _identity_value_is_excluded(principal_id)
        or _identity_value_is_excluded(machine_id)
        or literal_machine == "d1-relay"
        or literal_principal.startswith("nocturne-deploy-verify-")
    )


def split_gates(
    examples: Sequence[LearningExample],
    *,
    holdout_fraction: float,
) -> tuple[tuple[LearningExample, ...], tuple[LearningExample, ...], datetime]:
    """Create one deterministic newest-gates holdout without row leakage."""

    grouped: dict[UUID, list[LearningExample]] = defaultdict(list)
    for example in examples:
        grouped[example.injection_id].append(example)
    gates = sorted(
        grouped.items(),
        key=lambda item: (max(example.ts for example in item[1]), item[0].int),
    )
    if len(gates) < 2:
        raise ValueError("at least two eligible gates are required")
    holdout_count = max(1, math.ceil(len(gates) * holdout_fraction))
    holdout_count = min(holdout_count, len(gates) - 1)
    training_gates = gates[:-holdout_count]
    holdout_gates = gates[-holdout_count:]
    training = tuple(example for _, rows in training_gates for example in rows)
    holdout = tuple(example for _, rows in holdout_gates for example in rows)
    cutoff = max(example.ts for example in training)
    return training, holdout, cutoff


def fit_pairwise(
    examples: Sequence[LearningExample],
    *,
    incumbent_weights: Sequence[float],
    incumbent_thread_weight: float = 0.0,
    incumbent_where_weight: float = 0.0,
    incumbent_tau: float = 0.55,
    incumbent_memory_context_share: float = 0.10,
    share_boundaries: Sequence[ShareBoundary] = (),
    tune_share_and_tau: bool = False,
    settings: FitSettings,
) -> FitResult:
    """Solve the full convex squared-hinge objective by projected gradient."""

    if len(incumbent_weights) != len(FEATURE_NAMES):
        raise ValueError("incumbent weights must have six values")
    weights = _project_simplex(tuple(float(value) for value in incumbent_weights))
    pairs = _pairs(examples)
    if not pairs:
        raise ValueError("at least one positive-negative training pair is required")
    memory_ids = sorted(
        {example.memory_id for pair in pairs for example in pair[:2]}, key=lambda item: item.int
    )
    biases = {memory_id: 0.0 for memory_id in memory_ids}
    lipschitz = 2.0 * settings.bias_l2
    for positive, negative, actor_weight in pairs:
        positive_features, _ = _linearized(
            positive, incumbent_thread_weight, incumbent_where_weight
        )
        negative_features, _ = _linearized(
            negative, incumbent_thread_weight, incumbent_where_weight
        )
        feature_delta = tuple(
            left - right for left, right in zip(positive_features, negative_features, strict=True)
        )
        pair_norm = math.fsum(value * value for value in feature_delta) + 2.0
        lipschitz += 2.0 * actor_weight * pair_norm
    step = 1.0 / max(lipschitz, 1.0)
    objective = math.inf
    iterations = 0
    for iteration in range(1, 10_001):
        iterations = iteration
        current_objective, weight_gradient, bias_gradient = _objective_and_gradient(
            weights,
            biases,
            pairs,
            thread_weight=incumbent_thread_weight,
            where_weight=incumbent_where_weight,
            settings=settings,
        )
        next_weights = _project_simplex(
            tuple(
                value - step * gradient
                for value, gradient in zip(weights, weight_gradient, strict=True)
            )
        )
        next_biases = {
            memory_id: biases[memory_id] - step * bias_gradient[memory_id]
            for memory_id in memory_ids
        }
        next_objective = _objective(
            next_weights,
            next_biases,
            pairs,
            thread_weight=incumbent_thread_weight,
            where_weight=incumbent_where_weight,
            settings=settings,
        )
        change = max(
            max(abs(left - right) for left, right in zip(weights, next_weights, strict=True)),
            max(abs(biases[item] - next_biases[item]) for item in memory_ids),
        )
        improvement = current_objective - next_objective
        weights = next_weights
        biases = next_biases
        objective = next_objective
        if change <= 1e-11 or (0.0 <= improvement <= 1e-13):
            break
    normalized_biases = {
        memory_id: value for memory_id, value in biases.items() if abs(value) > 1e-12
    }
    thread_weight, thread_iterations, objective = _fit_thread_weight(
        weights,
        biases,
        pairs,
        initial=incumbent_thread_weight,
        where_weight=incumbent_where_weight,
        settings=settings,
    )
    where_weight, where_iterations, objective = _fit_where_weight(
        weights,
        biases,
        pairs,
        initial=incumbent_where_weight,
        thread_weight=thread_weight,
        settings=settings,
    )
    project_offsets = fit_project_offsets(
        examples,
        weights=weights,
        biases=normalized_biases,
        thread_weight=thread_weight,
        where_weight=where_weight,
        settings=settings,
    )
    tau = incumbent_tau
    memory_context_share = incumbent_memory_context_share
    boundary_iterations = 0
    if tune_share_and_tau:
        tau, tau_iterations = _fit_tau(
            examples,
            weights=weights,
            biases=biases,
            thread_weight=thread_weight,
            where_weight=where_weight,
            incumbent=incumbent_tau,
        )
        memory_context_share, share_iterations = _fit_share(
            share_boundaries,
            incumbent=incumbent_memory_context_share,
        )
        boundary_iterations = tau_iterations + share_iterations
    return FitResult(
        weights=weights,
        thread_weight=thread_weight,
        where_weight=where_weight,
        tau=tau,
        memory_context_share=memory_context_share,
        share_tau_active=tune_share_and_tau,
        bias_offsets=normalized_biases,
        pair_count=len(pairs),
        iterations=iterations + thread_iterations + where_iterations + boundary_iterations,
        objective=objective,
        project_offsets=project_offsets,
    )


def fit_project_offsets(
    examples: Sequence[LearningExample],
    *,
    weights: Sequence[float],
    biases: Mapping[UUID, float],
    thread_weight: float,
    where_weight: float,
    settings: FitSettings,
) -> dict[str, dict[str, float]]:
    """Fit project residuals AFTER globals, from zero, with L2 shrinkage to globals."""
    result = {}
    for project in sorted({row.project_key for row in examples if row.project_key is not None}):
        pairs = _pairs([row for row in examples if row.project_key == project])
        if not pairs:
            continue
        deltas = []
        for positive, negative, actor_weight in pairs:
            left, _ = _linearized(positive, thread_weight, where_weight)
            right, _ = _linearized(negative, thread_weight, where_weight)
            delta = tuple(a - b for a, b in zip(left, right, strict=True))
            baseline = (
                _example_score(positive, weights, thread_weight, where_weight)
                - _example_score(negative, weights, thread_weight, where_weight)
                + biases.get(positive.memory_id, 0)
                - biases.get(negative.memory_id, 0)
            )
            deltas.append((delta, baseline, actor_weight))
        offsets = [0.0] * len(FEATURE_NAMES)
        step = 1 / max(
            1,
            2 * settings.bias_l2
            + sum(2 * weight * math.fsum(x * x for x in delta) for delta, _, weight in deltas),
        )
        for _ in range(10000):
            gradient = [2 * settings.bias_l2 * value for value in offsets]
            for delta, baseline, actor_weight in deltas:
                hinge = max(
                    0,
                    settings.pair_margin
                    - baseline
                    - math.fsum(a * b for a, b in zip(offsets, delta, strict=True)),
                )
                for index, value in enumerate(delta):
                    gradient[index] -= 2 * actor_weight * hinge * value
            effective = _project_simplex(
                tuple(
                    weight + offset - step * grad
                    for weight, offset, grad in zip(weights, offsets, gradient, strict=True)
                )
            )
            updated = [a - b for a, b in zip(effective, weights, strict=True)]
            change = max(abs(a - b) for a, b in zip(updated, offsets, strict=True))
            offsets = updated
            if change < 1e-11:
                break
        result[project] = dict(zip(FEATURE_NAMES, offsets, strict=True))
    return result


def recorded_score(
    examples: Iterable[LearningExample],
    *,
    share_boundaries: Iterable[ShareBoundary] = (),
    memory_context_share: float = 0.10,
) -> ReplayScore:
    """Score the incumbent decisions that actually served the held-out gates."""

    score = _replay_score(
        examples,
        predicted=lambda example: example.recorded_injected,
    )
    return _with_share_score(score, share_boundaries, memory_context_share)


def challenger_score(
    examples: Iterable[LearningExample],
    *,
    weights: Sequence[float],
    bias_offsets: Mapping[UUID, float],
    thread_weight: float = 0.0,
    where_weight: float = 0.0,
    tau: float,
    share_boundaries: Iterable[ShareBoundary] = (),
    memory_context_share: float = 0.10,
    project_offsets: Mapping[str, Mapping[str, float]] | None = None,
    axes: Mapping[str, AxisNomination] | None = None,
) -> ReplayScore:
    """Score one fitted challenger against the held-out binary dispositions."""

    def predicted(example: LearningExample) -> bool:
        if example.shown_as == "pinned":
            return True
        offsets = (project_offsets or {}).get(example.project_key, {})
        effective = tuple(
            value + offsets.get(name, 0.0)
            for name, value in zip(FEATURE_NAMES, weights, strict=True)
        )
        if min(effective) < 0:
            effective = _project_simplex(effective)
        score = _example_score(example, effective, thread_weight, where_weight)
        score = (
            apply_axes(score - example.baseline_bias, example_features(example), axes or {})
            + example.baseline_bias
        )
        score += bias_offsets.get(example.memory_id, 0.0)
        return score >= tau

    score = _replay_score(examples, predicted=predicted)
    return _with_share_score(score, share_boundaries, memory_context_share)


def example_features(example: LearningExample) -> dict[str, float | None]:
    return {
        **dict(zip(FEATURE_NAMES, example.features, strict=True)),
        "loc": example.location_feature,
        "thread": example.thread_feature,
        "where": example.where_feature,
    }


def challenger_wins(
    incumbent: ReplayScore,
    challenger: ReplayScore,
    *,
    margin: Decimal,
    incumbent_memory_context_share: float | None = None,
    challenger_memory_context_share: float | None = None,
    incumbent_tau: float | None = None,
    challenger_tau: float | None = None,
) -> bool:
    """Apply the real-margin rule, then the exact cheaper-at-tie exception."""

    improvement = incumbent.weighted_disagreements - challenger.weighted_disagreements
    if improvement >= margin:
        return True
    if improvement != 0:
        return False
    incumbent_cost = (
        incumbent.injected_tokens,
        incumbent_memory_context_share if incumbent_memory_context_share is not None else 1.0,
        -(incumbent_tau if incumbent_tau is not None else 0.0),
    )
    challenger_cost = (
        challenger.injected_tokens,
        challenger_memory_context_share if challenger_memory_context_share is not None else 1.0,
        -(challenger_tau if challenger_tau is not None else 0.0),
    )
    return challenger_cost < incumbent_cost


def canonical_digest(
    *,
    incumbent_version: str,
    training: Sequence[LearningExample],
    holdout: Sequence[LearningExample],
    share_boundaries: Sequence[ShareBoundary] = (),
    settings: Mapping[str, object],
) -> str:
    """Hash every authority input used by a proposal in canonical order."""

    payload = {
        "incumbent_version": incumbent_version,
        "training": [_canonical_example(item) for item in training],
        "holdout": [_canonical_example(item) for item in holdout],
        "share_boundaries": [_canonical_boundary(item) for item in share_boundaries],
        "settings": settings,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _pairs(
    examples: Sequence[LearningExample],
) -> tuple[tuple[LearningExample, LearningExample, float], ...]:
    grouped: dict[UUID, list[LearningExample]] = defaultdict(list)
    for example in examples:
        grouped[example.injection_id].append(example)
    pairs: list[tuple[LearningExample, LearningExample, float]] = []
    for injection_id in sorted(grouped, key=lambda item: item.int):
        rows = grouped[injection_id]
        positives = sorted(
            (row for row in rows if row.target_injected), key=lambda item: item.event_uid
        )
        negatives = sorted(
            (row for row in rows if not row.target_injected), key=lambda item: item.event_uid
        )
        for positive in positives:
            for negative in negatives:
                pairs.append(
                    (
                        positive,
                        negative,
                        float(min(positive.actor_weight, negative.actor_weight)),
                    )
                )
    return tuple(pairs)


def _objective_and_gradient(
    weights: Sequence[float],
    biases: Mapping[UUID, float],
    pairs: Sequence[tuple[LearningExample, LearningExample, float]],
    *,
    thread_weight: float,
    where_weight: float,
    settings: FitSettings,
) -> tuple[float, list[float], dict[UUID, float]]:
    weight_gradient = [0.0] * len(FEATURE_NAMES)
    bias_gradient = {
        memory_id: 2.0 * settings.bias_l2 * value for memory_id, value in biases.items()
    }
    objective = settings.bias_l2 * math.fsum(value * value for value in biases.values())
    for positive, negative, actor_weight in pairs:
        positive_features, positive_constant = _linearized(positive, thread_weight, where_weight)
        negative_features, negative_constant = _linearized(negative, thread_weight, where_weight)
        feature_delta = tuple(
            left - right for left, right in zip(positive_features, negative_features, strict=True)
        )
        difference = math.fsum(
            weight * delta for weight, delta in zip(weights, feature_delta, strict=True)
        )
        difference += positive_constant - negative_constant
        difference += biases[positive.memory_id] - biases[negative.memory_id]
        hinge = settings.pair_margin - difference
        if hinge <= 0.0:
            continue
        objective += actor_weight * hinge * hinge
        factor = -2.0 * actor_weight * hinge
        for index, delta in enumerate(feature_delta):
            weight_gradient[index] += factor * delta
        bias_gradient[positive.memory_id] += factor
        bias_gradient[negative.memory_id] -= factor
    return objective, weight_gradient, bias_gradient


def _objective(
    weights: Sequence[float],
    biases: Mapping[UUID, float],
    pairs: Sequence[tuple[LearningExample, LearningExample, float]],
    *,
    thread_weight: float,
    where_weight: float,
    settings: FitSettings,
) -> float:
    return _objective_and_gradient(
        weights,
        biases,
        pairs,
        thread_weight=thread_weight,
        where_weight=where_weight,
        settings=settings,
    )[0]


def _linearized(
    example: LearningExample,
    thread_weight: float,
    where_weight: float,
) -> tuple[tuple[float, ...], float]:
    """Express one score as six linear weights plus a fixed locality constant."""

    feature_scale = 1.0
    constant = example.baseline_bias
    if example.location_feature is not None:
        feature_scale *= 1.0 - example.location_weight
        constant += example.location_weight * example.location_feature
    if example.thread_feature is not None:
        feature_scale *= 1.0 - thread_weight
        location_constant = constant - example.baseline_bias
        constant = (
            example.baseline_bias
            + (1.0 - thread_weight) * location_constant
            + thread_weight * example.thread_feature
        )
    if example.where_feature is not None:
        feature_scale *= 1.0 - where_weight
        locality_constant = constant - example.baseline_bias
        constant = (
            example.baseline_bias
            + (1.0 - where_weight) * locality_constant
            + where_weight * example.where_feature
        )
    return tuple(feature_scale * value for value in example.features), constant


def _example_score(
    example: LearningExample,
    weights: Sequence[float],
    thread_weight: float,
    where_weight: float,
) -> float:
    features, constant = _linearized(example, thread_weight, where_weight)
    return (
        math.fsum(weight * feature for weight, feature in zip(weights, features, strict=True))
        + constant
    )


def _fit_thread_weight(
    weights: Sequence[float],
    biases: Mapping[UUID, float],
    pairs: Sequence[tuple[LearningExample, LearningExample, float]],
    *,
    initial: float,
    where_weight: float,
    settings: FitSettings,
) -> tuple[float, int, float]:
    """Fit the bounded thread coefficient while holding the six weights and biases fixed."""

    thread_weight = min(max(float(initial), 0.0), 1.0 - 1e-12)
    derivatives: list[float] = []
    for positive, negative, _ in pairs:
        derivatives.append(
            _thread_derivative(positive, weights, where_weight)
            - _thread_derivative(negative, weights, where_weight)
        )
    lipschitz = 2.0 * math.fsum(
        actor_weight * derivative * derivative
        for (_, _, actor_weight), derivative in zip(pairs, derivatives, strict=True)
    )
    if lipschitz <= 0.0:
        return (
            thread_weight,
            0,
            _objective(
                weights,
                biases,
                pairs,
                thread_weight=thread_weight,
                where_weight=where_weight,
                settings=settings,
            ),
        )
    step = 1.0 / lipschitz
    iterations = 0
    for iteration in range(1, 10_001):
        iterations = iteration
        gradient = 0.0
        for (positive, negative, actor_weight), derivative in zip(pairs, derivatives, strict=True):
            difference = _example_score(positive, weights, thread_weight, where_weight)
            difference -= _example_score(negative, weights, thread_weight, where_weight)
            difference += biases[positive.memory_id] - biases[negative.memory_id]
            hinge = settings.pair_margin - difference
            if hinge > 0.0:
                gradient += -2.0 * actor_weight * hinge * derivative
        next_weight = min(max(thread_weight - step * gradient, 0.0), 1.0 - 1e-12)
        if abs(next_weight - thread_weight) <= 1e-11:
            thread_weight = next_weight
            break
        thread_weight = next_weight
    return (
        thread_weight,
        iterations,
        _objective(
            weights,
            biases,
            pairs,
            thread_weight=thread_weight,
            where_weight=where_weight,
            settings=settings,
        ),
    )


def _thread_derivative(
    example: LearningExample,
    weights: Sequence[float],
    where_weight: float,
) -> float:
    if example.thread_feature is None:
        return 0.0
    pre_thread = math.fsum(
        weight * feature for weight, feature in zip(weights, example.features, strict=True)
    )
    if example.location_feature is not None:
        pre_thread = (
            1.0 - example.location_weight
        ) * pre_thread + example.location_weight * example.location_feature
    return (1.0 - where_weight) * (example.thread_feature - pre_thread)


def _fit_where_weight(
    weights: Sequence[float],
    biases: Mapping[UUID, float],
    pairs: Sequence[tuple[LearningExample, LearningExample, float]],
    *,
    initial: float,
    thread_weight: float,
    settings: FitSettings,
) -> tuple[float, int, float]:
    """Fit the bounded birthplace-folder coefficient after the thread coefficient."""

    where_weight = min(max(float(initial), 0.0), 1.0 - 1e-12)
    derivatives = [
        _where_derivative(positive, weights, thread_weight)
        - _where_derivative(negative, weights, thread_weight)
        for positive, negative, _ in pairs
    ]
    lipschitz = 2.0 * math.fsum(
        actor_weight * derivative * derivative
        for (_, _, actor_weight), derivative in zip(pairs, derivatives, strict=True)
    )
    if lipschitz <= 0.0:
        return (
            where_weight,
            0,
            _objective(
                weights,
                biases,
                pairs,
                thread_weight=thread_weight,
                where_weight=where_weight,
                settings=settings,
            ),
        )
    step = 1.0 / lipschitz
    iterations = 0
    for iteration in range(1, 10_001):
        iterations = iteration
        gradient = 0.0
        for (positive, negative, actor_weight), derivative in zip(pairs, derivatives, strict=True):
            difference = _example_score(positive, weights, thread_weight, where_weight)
            difference -= _example_score(negative, weights, thread_weight, where_weight)
            difference += biases[positive.memory_id] - biases[negative.memory_id]
            hinge = settings.pair_margin - difference
            if hinge > 0.0:
                gradient += -2.0 * actor_weight * hinge * derivative
        next_weight = min(max(where_weight - step * gradient, 0.0), 1.0 - 1e-12)
        if abs(next_weight - where_weight) <= 1e-11:
            where_weight = next_weight
            break
        where_weight = next_weight
    return (
        where_weight,
        iterations,
        _objective(
            weights,
            biases,
            pairs,
            thread_weight=thread_weight,
            where_weight=where_weight,
            settings=settings,
        ),
    )


def _where_derivative(
    example: LearningExample,
    weights: Sequence[float],
    thread_weight: float,
) -> float:
    if example.where_feature is None:
        return 0.0
    pre_where = math.fsum(
        weight * feature for weight, feature in zip(weights, example.features, strict=True)
    )
    if example.location_feature is not None:
        pre_where = (
            1.0 - example.location_weight
        ) * pre_where + example.location_weight * example.location_feature
    if example.thread_feature is not None:
        pre_where = (1.0 - thread_weight) * pre_where + thread_weight * example.thread_feature
    return example.where_feature - pre_where


def _fit_tau(
    examples: Sequence[LearningExample],
    *,
    weights: Sequence[float],
    biases: Mapping[UUID, float],
    thread_weight: float,
    where_weight: float,
    incumbent: float,
) -> tuple[float, int]:
    """Choose the cheapest binary threshold among exact hundredth controls."""

    candidates = sorted({incumbent, *(index / 100 for index in range(101))})
    best = incumbent
    best_key: tuple[Decimal, int, float] | None = None
    for candidate in candidates:
        wrong = Decimal(0)
        injected_tokens = 0
        for example in examples:
            selected = example.shown_as == "pinned" or (
                _example_score(example, weights, thread_weight, where_weight)
                + biases.get(example.memory_id, 0.0)
                >= candidate
            )
            if selected:
                injected_tokens += example.body_tokens
            if selected != example.target_injected:
                wrong += example.actor_weight
        key = (wrong, injected_tokens, -candidate)
        if best_key is None or key < best_key:
            best_key = key
            best = candidate
    return best, len(candidates)


def _fit_share(
    boundaries: Sequence[ShareBoundary],
    *,
    incumbent: float,
) -> tuple[float, int]:
    """Fit room pressure on the same bounded hundredth-scale control."""

    if not boundaries:
        return incumbent, 0
    candidates = sorted(
        {
            incumbent,
            *(
                index / 100
                for index in range(
                    round(MIN_MEMORY_CONTEXT_SHARE * 100),
                    round(MAX_MEMORY_CONTEXT_SHARE * 100) + 1,
                )
            ),
        }
    )
    best = incumbent
    best_key: tuple[Decimal, int, float] | None = None
    for candidate in candidates:
        wrong = Decimal(0)
        count = 0
        for boundary in boundaries:
            fits = candidate + 1e-12 >= boundary.required_share
            if fits != boundary.target_at_least:
                count += 1
                wrong += boundary.actor_weight
        key = (wrong, count, candidate)
        if best_key is None or key < best_key:
            best_key = key
            best = candidate
    return best, len(candidates)


def _with_share_score(
    score: ReplayScore,
    boundaries: Iterable[ShareBoundary],
    memory_context_share: float,
) -> ReplayScore:
    share_disagreements = 0
    weighted_share_disagreements = Decimal(0)
    for boundary in boundaries:
        fits = memory_context_share + 1e-12 >= boundary.required_share
        if fits != boundary.target_at_least:
            share_disagreements += 1
            weighted_share_disagreements += boundary.actor_weight
    return ReplayScore(
        disagreements=score.disagreements + share_disagreements,
        weighted_disagreements=(score.weighted_disagreements + weighted_share_disagreements),
        injected_tokens=score.injected_tokens,
        share_disagreements=share_disagreements,
        weighted_share_disagreements=weighted_share_disagreements,
    )


def _replay_score(
    examples: Iterable[LearningExample],
    *,
    predicted,
) -> ReplayScore:
    disagreements = 0
    weighted_disagreements = Decimal(0)
    injected_tokens = 0
    for example in examples:
        prediction = bool(predicted(example))
        if prediction:
            injected_tokens += example.body_tokens
        if prediction != example.target_injected:
            disagreements += 1
            weighted_disagreements += example.actor_weight
    return ReplayScore(
        disagreements=disagreements,
        weighted_disagreements=weighted_disagreements,
        injected_tokens=injected_tokens,
    )


def _identity_value_is_excluded(value: str) -> bool:
    normalized = _normalize_identity(value)
    if normalized in {"test", "fixture", "verification"}:
        return True
    tokens = tuple(part for part in normalized.replace(":", "-").split("-") if part)
    return any(token in {"test", "fixture", "verification"} for token in tokens)


def _normalize_identity(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def _canonical_example(example: LearningExample) -> dict[str, object]:
    return {
        "event_uid": example.event_uid,
        "injection_id": str(example.injection_id),
        "memory_id": str(example.memory_id),
        "ts": example.ts.isoformat(),
        "features": list(example.features),
        "project_key": example.project_key,
        "baseline_bias": example.baseline_bias,
        "target_injected": example.target_injected,
        "actor_weight": str(example.actor_weight),
        "shown_as": example.shown_as,
        "body_tokens": example.body_tokens,
        "location_feature": example.location_feature,
        "location_weight": example.location_weight,
        "thread_feature": example.thread_feature,
        "where_feature": example.where_feature,
        "where_weight": example.where_weight,
    }


def _canonical_boundary(boundary: ShareBoundary) -> dict[str, object]:
    return {
        "event_uid": boundary.event_uid,
        "injection_id": str(boundary.injection_id),
        "required_share": boundary.required_share,
        "target_at_least": boundary.target_at_least,
        "actor_weight": str(boundary.actor_weight),
        "kind": boundary.kind,
    }
