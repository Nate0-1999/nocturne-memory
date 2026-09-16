"""Curate transparent scorer structure from the same recorded training gates."""

import math
from itertools import combinations

from spine.inject.axes import AxisNomination, apply_axes
from spine.learner.model import (
    FEATURE_NAMES,
    LearningExample,
    _example_score,
    _pairs,
    example_features,
)

LABELS = dict(
    zip(
        FEATURE_NAMES,
        ("Meaning", "Keywords", "Recency", "Project", "Use count", "Past choices"),
        strict=True,
    )
)


def nominate_axes(examples, *, fit, incumbent, settings, corpus_fingerprint):
    """Nominate one training-selected scalar; holdout decides whether it may be proposed.

    Products describe conjunctions (e.g. keyword match within project). No new
    collection, generated code, or holdout-driven search is involved. All prior
    axes are retained in the registry; retirement sets a zero coefficient.
    """
    pairs = _pairs(examples)
    axes = dict(incumbent.axes)

    def score(row: LearningExample, current_axes):
        offset = fit.project_offsets.get(row.project_key, {})
        weights = tuple(
            value + offset.get(name, 0)
            for name, value in zip(FEATURE_NAMES, fit.weights, strict=True)
        )
        base = _example_score(row, weights, fit.thread_weight, fit.where_weight)
        return (
            apply_axes(base - row.baseline_bias, example_features(row), current_axes)
            + row.baseline_bias
            + fit.bias_offsets.get(row.memory_id, 0)
        )

    def objective(current_axes):
        return math.fsum(
            weight
            * max(0, settings.pair_margin - score(left, current_axes) + score(right, current_axes))
            ** 2
            for left, right, weight in pairs
        ) + settings.bias_l2 * math.fsum(axis.weight**2 for axis in current_axes.values())

    # Deterministic bounded one-dimensional minimization, including EXACT zero.
    def fitted(axis):
        def at(weight):
            candidate = axis.model_copy(update={"weight": weight})
            return objective({**axes, axis.name: candidate}), candidate

        low, high = 0.0, 1 - 1e-12
        for _ in range(40):
            left, right = low + (high - low) / 3, high - (high - low) / 3
            if at(left)[0] <= at(right)[0]:
                high = right
            else:
                low = left
        return min((at(0), at((low + high) / 2), at(1 - 1e-12)), key=lambda pair: pair[0])

    for name, axis in sorted(axes.items()):
        if axis.action == "axis_retire":
            continue
        _, updated = fitted(axis)
        zero_generations = (
            int(axis.provenance.get("zero_generations", 0)) + 1 if updated.weight < 1e-6 else 0
        )
        axes[name] = updated.model_copy(
            update={
                "weight": 0.0 if zero_generations >= 2 else updated.weight,
                "action": "axis_retire" if zero_generations >= 2 else "axis_add",
                "rationale": "Coefficient stayed near zero across fitted generations."
                if zero_generations >= 2
                else axis.rationale,
                "provenance": {
                    **axis.provenance,
                    "zero_generations": zero_generations,
                    "latest_corpus": corpus_fingerprint,
                },
            }
        )

    best = objective(axes), None
    for left, right in combinations(FEATURE_NAMES, 2):
        name = f"{left}_and_{right}"
        if name in axes:
            continue
        candidate = AxisNomination(
            name=name,
            label=f"{LABELS[left]} × {LABELS[right]}",
            inputs=(left, right),
            weight=0,
            action="axis_add",
            rationale="Joint evidence reduces training pair disagreements.",
            provenance={
                "curator": "recorded-scalar-products-v1",
                "corpus_fingerprint": corpus_fingerprint,
                "event_uids": [row.event_uid for row in examples],
                "zero_generations": 0,
            },
        )
        loss, candidate = fitted(candidate)
        if loss < best[0] - 1e-9 and candidate.weight > 1e-6:
            best = loss, candidate
    if best[1] is not None:
        axes[best[1].name] = best[1]
    return axes
