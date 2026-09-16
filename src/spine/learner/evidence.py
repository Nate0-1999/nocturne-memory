"""Canonical A-031 evidence projection shared by retraining and read models."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from spine.db.models import InjectionEvent, InjectionEventAnnotation
from spine.inject.axes import apply_axes
from spine.inject.scorer import ScorerConfig as RuntimeScorerConfig
from spine.learner.model import (
    EXPLICIT_POSITIVE_OUTCOMES,
    FEATURE_NAMES,
    NEGATIVE_OUTCOMES,
    LearningExample,
    ShareBoundary,
    disposition,
    identity_is_excluded,
)
from spine.tokens import cl100k_token_count


class LearnerDataError(RuntimeError):
    """The append-only evidence cannot be replayed without guessing."""


@dataclass(frozen=True, slots=True)
class LearningEvidence:
    examples: tuple[LearningExample, ...]
    share_boundaries: tuple[ShareBoundary, ...]
    hygiene_excluded_dispositions: int


def project_learning_evidence(
    rows: list[InjectionEvent],
    annotations: list[InjectionEventAnnotation],
    configs: Mapping[str, RuntimeScorerConfig],
    *,
    passive_discount: Decimal,
) -> LearningEvidence:
    """Apply A-031 grading and whole-gate hygiene exactly once for all consumers."""

    grouped: dict[UUID, list[InjectionEvent]] = defaultdict(list)
    for row in rows:
        grouped[row.injection_id].append(row)
    verification_only = {
        annotation.target_event_uid
        for annotation in annotations
        if annotation.kind == "verification_only"
    }
    excluded = {
        injection_id
        for injection_id, members in grouped.items()
        if any(
            member.event_uid in verification_only
            or identity_is_excluded(principal_id=member.principal_id, machine_id=member.machine_id)
            for member in members
        )
    }
    excluded_dispositions = sum(
        disposition(row.outcome, row.actor_class, passive_discount=passive_discount) is not None
        for row in rows
        if row.injection_id in excluded
    )
    examples: list[LearningExample] = []
    for row in rows:
        if row.injection_id in excluded:
            continue
        labeled = disposition(
            row.outcome,
            row.actor_class,
            passive_discount=passive_discount,
        )
        if labeled is None:
            continue
        source = configs.get(row.scorer_version)
        if source is None:
            raise LearnerDataError(
                f"event {row.event_uid} references missing scorer {row.scorer_version!r}"
            )
        features = _features(row)
        location = _optional_feature(row, "loc")
        thread = _optional_feature(row, "thread")
        where = _optional_feature(row, "where")
        pre_location = math.fsum(
            weight * feature
            for weight, feature in zip(
                (
                    getattr(source.weights_for_project(getattr(row, "project_key", None)), name)
                    for name in FEATURE_NAMES
                ),
                features,
                strict=True,
            )
        )
        localized = (
            pre_location
            if location is None
            else (1.0 - source.params.location_weight) * pre_location
            + source.params.location_weight * location
        )
        localized = (
            localized
            if thread is None
            else (1.0 - source.params.thread_weight) * localized
            + source.params.thread_weight * thread
        )
        localized = (
            localized
            if where is None
            else (1.0 - source.params.where_weight) * localized + source.params.where_weight * where
        )
        localized = apply_axes(localized, row.features, source.axes)
        baseline_bias = float(row.score) - localized
        baseline_bias -= source.bias_offset(row.memory_id)
        body = _frozen_body(row)
        target_injected, actor_weight = labeled
        examples.append(
            LearningExample(
                event_uid=row.event_uid,
                injection_id=row.injection_id,
                memory_id=row.memory_id,
                ts=row.ts,
                features=features,
                baseline_bias=baseline_bias,
                target_injected=target_injected,
                actor_weight=actor_weight,
                shown_as=row.shown_as,  # type: ignore[arg-type]
                body_tokens=cl100k_token_count(body),
                location_feature=location,
                location_weight=source.params.location_weight,
                thread_feature=thread,
                thread_id=getattr(row, "thread_id", None),
                where_feature=where,
                where_weight=source.params.where_weight,
                project_key=getattr(row, "project_key", None),
            )
        )
    return LearningEvidence(
        examples=tuple(examples),
        share_boundaries=_share_boundaries(
            rows=[row for row in rows if row.injection_id not in excluded],
            configs=configs,
            passive_discount=passive_discount,
        ),
        hygiene_excluded_dispositions=excluded_dispositions,
    )


def _share_boundaries(
    *,
    rows: list[InjectionEvent],
    configs: Mapping[str, RuntimeScorerConfig],
    passive_discount: Decimal,
) -> tuple[ShareBoundary, ...]:
    """Project D.2 133-134 room-up/down evidence without minting feedback."""

    explicit_positive_weights: dict[UUID, Decimal] = {}
    for row in rows:
        if row.outcome not in EXPLICIT_POSITIVE_OUTCOMES:
            continue
        labeled = disposition(row.outcome, row.actor_class, passive_discount=passive_discount)
        if labeled is not None and labeled[0]:
            explicit_positive_weights[row.memory_id] = max(
                explicit_positive_weights.get(row.memory_id, Decimal(0)),
                labeled[1],
            )

    grouped: dict[UUID, list[InjectionEvent]] = defaultdict(list)
    for row in rows:
        grouped[row.injection_id].append(row)
    result: list[ShareBoundary] = []
    for injection_id in sorted(grouped, key=lambda value: value.int):
        members = grouped[injection_id]
        context_tokens = _context_tokens(members)
        if context_tokens is None:
            continue
        selected_regular = [row for row in members if row.shown_as == "injected"]
        selected_pins = [row for row in members if row.shown_as == "pinned"]
        regular_tokens = sum(cl100k_token_count(_frozen_body(row)) for row in selected_regular)
        pinned_tokens = sum(cl100k_token_count(_frozen_body(row)) for row in selected_pins)

        seen_cuts: set[UUID] = set()
        for row in sorted(members, key=lambda item: (item.rank, item.event_uid)):
            if (
                row.shown_as != "budget_cut"
                or row.memory_id in seen_cuts
                or row.memory_id not in explicit_positive_weights
            ):
                continue
            seen_cuts.add(row.memory_id)
            required = (regular_tokens + cl100k_token_count(_frozen_body(row))) / context_tokens
            result.append(
                ShareBoundary(
                    event_uid=row.event_uid,
                    injection_id=injection_id,
                    required_share=required,
                    target_at_least=True,
                    actor_weight=explicit_positive_weights[row.memory_id],
                    kind="valuable_budget_cut",
                )
            )

        if selected_regular:
            marginal = max(selected_regular, key=lambda row: (row.rank, row.event_uid))
            if marginal.outcome in NEGATIVE_OUTCOMES:
                result.append(
                    ShareBoundary(
                        event_uid=marginal.event_uid,
                        injection_id=injection_id,
                        required_share=regular_tokens / context_tokens,
                        target_at_least=False,
                        actor_weight=Decimal(1),
                        kind="marginal_removed",
                    )
                )
            elif marginal.outcome == "kept":
                result.append(
                    ShareBoundary(
                        event_uid=marginal.event_uid,
                        injection_id=injection_id,
                        required_share=regular_tokens / context_tokens,
                        target_at_least=False,
                        actor_weight=passive_discount,
                        kind="marginal_uncited",
                    )
                )

        if selected_pins:
            total_tokens = regular_tokens + pinned_tokens
            source = configs.get(members[0].scorer_version)
            if source is None:
                raise LearnerDataError(
                    f"event {members[0].event_uid} references missing scorer "
                    f"{members[0].scorer_version!r}"
                )
            share_tokens = _recorded_share_tokens(members, source, context_tokens)
            if total_tokens > share_tokens:
                first_pin = min(selected_pins, key=lambda row: (row.rank, row.event_uid))
                result.append(
                    ShareBoundary(
                        event_uid=first_pin.event_uid,
                        injection_id=injection_id,
                        required_share=total_tokens / context_tokens,
                        target_at_least=True,
                        actor_weight=Decimal(1),
                        kind="pin_overflow",
                    )
                )
    return tuple(result)


def _context_tokens(rows: list[InjectionEvent]) -> int | None:
    for row in rows:
        prepare = row.features.get("_prepare")
        value = prepare.get("model_context_tokens") if isinstance(prepare, Mapping) else None
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def _recorded_share_tokens(
    rows: list[InjectionEvent],
    source: RuntimeScorerConfig,
    context_tokens: int,
) -> int:
    for row in rows:
        prepare = row.features.get("_prepare")
        value = prepare.get("share_tokens") if isinstance(prepare, Mapping) else None
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    share_tokens = int(source.params.memory_context_share * context_tokens)
    if source.params.legacy_budget_tokens is not None:
        share_tokens = min(share_tokens, source.params.legacy_budget_tokens)
    return share_tokens


def _features(
    row: InjectionEvent,
) -> tuple[float, float, float, float, float, float]:
    values: list[float] = []
    for name in FEATURE_NAMES:
        value = row.features.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise LearnerDataError(f"event {row.event_uid} feature {name} is not numeric")
        normalized = float(value)
        if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
            raise LearnerDataError(f"event {row.event_uid} feature {name} is outside [0,1]")
        values.append(normalized)
    return tuple(values)  # type: ignore[return-value]


def _optional_feature(row: InjectionEvent, name: str) -> float | None:
    if name not in row.features:
        return None
    value = row.features[name]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LearnerDataError(f"event {row.event_uid} feature {name} is not numeric")
    normalized = float(value)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise LearnerDataError(f"event {row.event_uid} feature {name} is outside [0,1]")
    return normalized


def _frozen_body(row: InjectionEvent) -> str:
    memory = row.features.get("_memory")
    body = memory.get("body") if isinstance(memory, Mapping) else None
    if not isinstance(body, str):
        raise LearnerDataError(f"event {row.event_uid} has no frozen memory body")
    return body


def _weight_tuple(
    config: RuntimeScorerConfig,
) -> tuple[float, float, float, float, float, float]:
    weights = config.weights
    return (weights.sem, weights.kw, weights.time, weights.proj, weights.freq, weights.hist)


__all__ = [
    "LearnerDataError",
    "LearningEvidence",
    "project_learning_evidence",
]
