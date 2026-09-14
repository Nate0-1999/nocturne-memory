"""Pure, deterministic implementation of SPEC C.3 scorer v0."""

from __future__ import annotations

import math
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from fractions import Fraction
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

from spine.tokens import cl100k_token_count

_SECONDS_PER_DAY = 86_400
MIN_MEMORY_CONTEXT_SHARE = 0.01
MAX_MEMORY_CONTEXT_SHARE = 0.50
DEFAULT_MEMORY_CONTEXT_SHARE = 0.10
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "he",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "that",
        "the",
        "to",
        "was",
        "were",
        "will",
        "with",
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ScorerWeights:
    """The six linear feature weights for one immutable scorer version."""

    sem: float
    kw: float
    time: float
    proj: float
    freq: float
    hist: float


@dataclass(frozen=True, slots=True, kw_only=True)
class ScorerParams:
    """Selection and decay parameters for one immutable scorer version."""

    tau: float
    near_miss_k: int
    memory_context_share: float
    half_life_time_days: float
    half_life_hist_days: float
    candidate_pool: int
    location_weight: float = 0.0
    half_life_location_hops: float = 2.0
    thread_weight: float = 0.0
    where_weight: float = 0.0
    legacy_budget_tokens: int | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.tau <= 1.0:
            raise ValueError("tau must be between zero and one")
        if self.near_miss_k < 0:
            raise ValueError("near_miss_k must not be negative")
        if not MIN_MEMORY_CONTEXT_SHARE <= self.memory_context_share <= MAX_MEMORY_CONTEXT_SHARE:
            raise ValueError("memory_context_share must be between 0.01 and 0.50")
        if self.legacy_budget_tokens is not None and self.legacy_budget_tokens <= 0:
            raise ValueError("legacy budget_tokens must be positive")
        if self.half_life_time_days <= 0.0 or self.half_life_hist_days <= 0.0:
            raise ValueError("scorer half lives must be positive")
        if self.candidate_pool <= 0:
            raise ValueError("candidate_pool must be positive")
        if not 0.0 <= self.location_weight < 1.0:
            raise ValueError("location_weight must be at least zero and less than one")
        if self.half_life_location_hops <= 0.0:
            raise ValueError("location half life must be positive")
        if not 0.0 <= self.thread_weight < 1.0:
            raise ValueError("thread_weight must be at least zero and less than one")
        if not 0.0 <= self.where_weight < 1.0:
            raise ValueError("where_weight must be at least zero and less than one")


@dataclass(frozen=True, slots=True, kw_only=True)
class ScorerConfig:
    """Versioned scorer configuration loaded from ``scorer_config``."""

    version: str
    weights: ScorerWeights
    params: ScorerParams
    bias_offsets: Mapping[UUID, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("scorer version must not be blank")
        values = (
            self.weights.sem,
            self.weights.kw,
            self.weights.time,
            self.weights.proj,
            self.weights.freq,
            self.weights.hist,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("scorer weights must be finite")
        if any(not math.isfinite(value) for value in self.bias_offsets.values()):
            raise ValueError("scorer bias offsets must be finite")

    @classmethod
    def from_mappings(
        cls,
        *,
        version: str,
        weights: Mapping[str, Any],
        params: Mapping[str, Any],
    ) -> ScorerConfig:
        """Build the typed pure boundary from the two JSONB config objects."""

        learner = params.get("_learner", {})
        if not isinstance(learner, Mapping):
            raise ValueError("scorer _learner metadata must be an object")
        raw_bias_offsets = learner.get("bias_offsets", {})
        if not isinstance(raw_bias_offsets, Mapping):
            raise ValueError("scorer learner bias_offsets must be an object")
        bias_offsets: dict[UUID, float] = {}
        for memory_id, value in raw_bias_offsets.items():
            try:
                parsed_id = UUID(str(memory_id))
            except (TypeError, ValueError) as error:
                raise ValueError("scorer learner bias offset key must be a UUID") from error
            bias_offsets[parsed_id] = _finite_number(value, "bias offset")

        return cls(
            version=version,
            weights=ScorerWeights(
                sem=_number(weights, "sem"),
                kw=_number(weights, "kw"),
                time=_number(weights, "time"),
                proj=_number(weights, "proj"),
                freq=_number(weights, "freq"),
                hist=_number(weights, "hist"),
            ),
            params=ScorerParams(
                tau=_number(params, "tau"),
                near_miss_k=_integer(params, "near_miss_k"),
                memory_context_share=(
                    _number(params, "memory_context_share")
                    if "memory_context_share" in params
                    else _number(params, "budget_pct")
                ),
                half_life_time_days=_number(params, "half_life_time_days"),
                half_life_hist_days=_number(params, "half_life_hist_days"),
                candidate_pool=_integer(params, "candidate_pool"),
                location_weight=_optional_number(params, "location_weight", 0.0),
                half_life_location_hops=_optional_number(params, "half_life_location_hops", 2.0),
                thread_weight=_optional_number(params, "thread_weight", 0.0),
                where_weight=_optional_number(params, "where_weight", 0.0),
                legacy_budget_tokens=(
                    None if "memory_context_share" in params else _integer(params, "budget_tokens")
                ),
            ),
            bias_offsets=bias_offsets,
        )

    def bias_offset(self, memory_id: UUID) -> float:
        """Return this immutable version's learned per-memory score offset."""

        return self.bias_offsets.get(memory_id, 0.0)


@dataclass(frozen=True, slots=True, kw_only=True)
class ScoringCandidate:
    """Detached memory head and revision facts needed by scoring and its caller."""

    memory_id: UUID
    label: str
    body: str
    kind: str
    keywords: tuple[str, ...]
    embedding: tuple[float, ...]
    project_key: str | None
    origin_thread_id: UUID | None = None
    origin_path: str | None = None
    origin_location: str | None = None
    pin: bool
    updated_at: datetime
    last_human_edit_at: datetime | None
    stats: Mapping[str, Any]
    bias: float
    revision: int
    pool_sources: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class ScoreFeatures:
    """The explainability features persisted for every decision."""

    sem: float
    kw: float
    time: float
    proj: float
    freq: float
    hist: float
    loc: float | None = None
    thread: float | None = None
    where: float | None = None

    def as_dict(self) -> dict[str, float | None]:
        """Return the exact public feature object without scorer internals."""

        values: dict[str, float | None] = {
            "sem": self.sem,
            "kw": self.kw,
            "time": self.time,
            "proj": self.proj,
            "freq": self.freq,
            "hist": self.hist,
            "loc": self.loc,
            "thread": self.thread,
            "where": self.where,
        }
        if self.loc is None:
            del values["loc"]
        if self.thread is None:
            del values["thread"]
        if self.where is None:
            del values["where"]
        return values


@dataclass(frozen=True, slots=True, kw_only=True)
class ScoredCandidate:
    """One candidate with its complete-order rank and body token cost."""

    candidate: ScoringCandidate
    features: ScoreFeatures
    score: float
    rank: int
    token_cost: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ScoringSelection:
    """Cards returned by prepare plus deterministic budget accounting."""

    injected: tuple[ScoredCandidate, ...]
    near_misses: tuple[ScoredCandidate, ...]
    budget_cuts: tuple[ScoredCandidate, ...]
    unselected: tuple[ScoredCandidate, ...]
    regular_budget: int
    pin_token_cost: int
    regular_token_cost: int
    total_token_cost: int
    pinned_overflow_tokens: int


def score_and_select(
    *,
    prompt: str,
    query_embedding: Sequence[float],
    snapshot_ts: datetime,
    thread_project_key: str | None,
    thread_id: UUID | None = None,
    location_path: str | None = None,
    current_location: str | None = None,
    pinned_candidates: Sequence[ScoringCandidate],
    regular_candidates: Sequence[ScoringCandidate],
    locked_memory_ids: frozenset[UUID] = frozenset(),
    model_context_tokens: int,
    config: ScorerConfig,
) -> ScoringSelection:
    """Score an eligible pool and apply scorer-v0 pin, rank, and budget law."""

    if model_context_tokens <= 0:
        raise ValueError("model_context_tokens must be positive")

    query = _validated_vector(query_embedding, name="query embedding")
    pins = tuple(pinned_candidates)
    regular = tuple(regular_candidates)
    _validate_candidate_partition(pins, regular)

    keywords = prompt_keywords(prompt)
    pin_scores = [
        _score_candidate(
            candidate,
            query=query,
            prompt_keywords=keywords,
            snapshot_ts=snapshot_ts,
            thread_project_key=thread_project_key,
            thread_id=thread_id,
            location_path=location_path,
            current_location=current_location,
            weights=config.weights,
            params=config.params,
            learned_bias=config.bias_offset(candidate.memory_id),
        )
        for candidate in pins
    ]
    pin_scores.sort(key=lambda scored: scored.candidate.memory_id.int)
    pin_ids = frozenset(scored.candidate.memory_id for scored in pin_scores)
    locked_regular_ids = locked_memory_ids - pin_ids

    scored_pool = [
        _score_candidate(
            candidate,
            query=query,
            prompt_keywords=keywords,
            snapshot_ts=snapshot_ts,
            thread_project_key=thread_project_key,
            thread_id=thread_id,
            location_path=location_path,
            current_location=current_location,
            weights=config.weights,
            params=config.params,
            learned_bias=config.bias_offset(candidate.memory_id),
        )
        for candidate in regular
    ]
    locked_pool = [
        scored for scored in scored_pool if scored.candidate.memory_id in locked_regular_ids
    ]
    regular_pool = [
        scored for scored in scored_pool if scored.candidate.memory_id not in locked_regular_ids
    ]
    if len(locked_pool) != len(locked_regular_ids):
        raise ValueError("every locked memory must be an eligible candidate")
    locked_pool.sort(key=lambda scored: (-scored.score, scored.candidate.memory_id.int))
    regular_pool.sort(key=lambda scored: (-scored.score, scored.candidate.memory_id.int))

    ranked_pins = tuple(
        _with_rank(scored, rank=index) for index, scored in enumerate(pin_scores, start=1)
    )
    ranked_locked = tuple(
        _with_rank(scored, rank=index)
        for index, scored in enumerate(locked_pool, start=len(ranked_pins) + 1)
    )
    ranked_regular = tuple(
        _with_rank(scored, rank=index)
        for index, scored in enumerate(
            regular_pool, start=len(ranked_pins) + len(ranked_locked) + 1
        )
    )

    pin_token_cost = sum(scored.token_cost for scored in ranked_pins)
    locked_token_cost = sum(scored.token_cost for scored in ranked_locked)
    share = Fraction(str(config.params.memory_context_share))
    share_tokens = share.numerator * model_context_tokens // share.denominator
    if config.params.legacy_budget_tokens is not None:
        share_tokens = min(config.params.legacy_budget_tokens, share_tokens)
    remaining_budget = max(0, share_tokens - locked_token_cost)
    selected_regular: list[ScoredCandidate] = []
    unselected_regular: list[ScoredCandidate] = []
    budget_cuts: list[ScoredCandidate] = []

    for scored in ranked_regular:
        cut_by_budget = (
            scored.score >= config.params.tau
            and scored.token_cost > remaining_budget
        )
        selectable = (
            scored.score >= config.params.tau
            and scored.token_cost <= remaining_budget
        )
        if selectable:
            selected_regular.append(scored)
            remaining_budget -= scored.token_cost
        else:
            unselected_regular.append(scored)
            if cut_by_budget:
                budget_cuts.append(scored)

    regular_token_cost = locked_token_cost + sum(item.token_cost for item in selected_regular)
    total_token_cost = pin_token_cost + regular_token_cost
    return ScoringSelection(
        injected=(*ranked_pins, *ranked_locked, *selected_regular),
        near_misses=tuple(unselected_regular[: config.params.near_miss_k]),
        budget_cuts=tuple(budget_cuts),
        unselected=tuple(unselected_regular),
        regular_budget=share_tokens,
        pin_token_cost=pin_token_cost,
        regular_token_cost=regular_token_cost,
        total_token_cost=total_token_cost,
        pinned_overflow_tokens=min(
            pin_token_cost,
            max(0, total_token_cost - share_tokens),
        ),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class _UnrankedCandidate:
    candidate: ScoringCandidate
    features: ScoreFeatures
    score: float
    token_cost: int


def _score_candidate(
    candidate: ScoringCandidate,
    *,
    query: tuple[float, ...],
    semantic: float | None = None,
    prompt_keywords: frozenset[str],
    snapshot_ts: datetime,
    thread_project_key: str | None,
    thread_id: UUID,
    location_path: str | None,
    current_location: str | None = None,
    weights: ScorerWeights,
    params: ScorerParams,
    learned_bias: float = 0.0,
) -> _UnrankedCandidate:
    memory_keywords = set(_tokens(candidate.label))
    for keyword in candidate.keywords:
        memory_keywords.update(_tokens(keyword))
    keyword_overlap = len(prompt_keywords.intersection(memory_keywords)) / max(
        1, len(prompt_keywords)
    )

    features = ScoreFeatures(
        sem=(_semantic_similarity(candidate, query) if semantic is None else semantic),
        kw=min(1.0, keyword_overlap),
        time=_decay(
            snapshot_ts=snapshot_ts,
            event_ts=candidate.updated_at,
            half_life_days=params.half_life_time_days,
        ),
        proj=_project_feature(
            thread_project_key=thread_project_key,
            memory_project_key=candidate.project_key,
        ),
        freq=min(1.0, _citations(candidate.stats) / 10.0),
        hist=(
            0.0
            if candidate.last_human_edit_at is None
            else _decay(
                snapshot_ts=snapshot_ts,
                event_ts=candidate.last_human_edit_at,
                half_life_days=params.half_life_hist_days,
            )
        ),
        loc=_location_feature(
            location_path=location_path,
            origin_path=candidate.origin_path,
            thread_project_key=thread_project_key,
            memory_project_key=candidate.project_key,
            half_life_hops=params.half_life_location_hops,
        ),
        thread=_thread_feature(
            thread_id=thread_id,
            origin_thread_id=candidate.origin_thread_id,
        ),
        where=_where_feature(
            current_location=current_location,
            origin_location=candidate.origin_location,
        ),
    )
    base_score = math.fsum(
        (
            weights.sem * features.sem,
            weights.kw * features.kw,
            weights.time * features.time,
            weights.proj * features.proj,
            weights.freq * features.freq,
            weights.hist * features.hist,
        )
    )
    location_score = (
        base_score
        if features.loc is None
        else (1.0 - params.location_weight) * base_score + params.location_weight * features.loc
    )
    score = (
        location_score
        if features.thread is None
        else (1.0 - params.thread_weight) * location_score + params.thread_weight * features.thread
    )
    score = (
        score
        if features.where is None
        else (1.0 - params.where_weight) * score + params.where_weight * features.where
    )
    score += candidate.bias + learned_bias
    if not math.isfinite(score):
        raise ValueError(f"score for {candidate.memory_id} is not finite")
    return _UnrankedCandidate(
        candidate=candidate,
        features=features,
        score=_postgres_real(score),
        token_cost=cl100k_token_count(candidate.body),
    )


def _with_rank(scored: _UnrankedCandidate, *, rank: int) -> ScoredCandidate:
    return ScoredCandidate(
        candidate=scored.candidate,
        features=scored.features,
        score=scored.score,
        rank=rank,
        token_cost=scored.token_cost,
    )


def _validate_candidate_partition(
    pins: Sequence[ScoringCandidate], regular: Sequence[ScoringCandidate]
) -> None:
    if any(not candidate.pin for candidate in pins):
        raise ValueError("every pinned candidate must have pin=true")
    if any(candidate.pin for candidate in regular):
        raise ValueError("regular candidates must have pin=false")
    ids = [candidate.memory_id for candidate in (*pins, *regular)]
    if len(ids) != len(set(ids)):
        raise ValueError("candidate memory IDs must be unique")


def _tokens(value: str) -> tuple[str, ...]:
    lowered = value.lower()
    tokens: list[str] = []
    current: list[str] = []
    for character in lowered:
        if character.isalnum():
            current.append(character)
        elif current:
            tokens.append("".join(current))
            current.clear()
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


def prompt_keywords(value: str) -> frozenset[str]:
    """Return the C.3 normalized prompt terms shared by FTS and f_kw."""

    return frozenset(token for token in _tokens(value) if token not in _STOPWORDS)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    numerator = math.fsum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(math.fsum(value * value for value in left))
    right_norm = math.sqrt(math.fsum(value * value for value in right))
    cosine = numerator / (left_norm * right_norm)
    return min(1.0, max(-1.0, cosine))


def _vector_similarity(candidate: ScoringCandidate, query: tuple[float, ...]) -> float:
    embedding = _validated_vector(candidate.embedding, name=f"embedding for {candidate.memory_id}")
    if len(query) != len(embedding):
        raise ValueError(
            f"embedding for {candidate.memory_id} has {len(embedding)} dimensions; "
            f"query has {len(query)}"
        )
    return _cosine(query, embedding)


def _semantic_similarity(candidate: ScoringCandidate, query: tuple[float, ...]) -> float:
    return max(0.0, _vector_similarity(candidate, query))


def _decay(*, snapshot_ts: datetime, event_ts: datetime, half_life_days: float) -> float:
    age_days = max(0.0, (snapshot_ts - event_ts).total_seconds() / _SECONDS_PER_DAY)
    return 2.0 ** (-age_days / half_life_days)


def _project_feature(*, thread_project_key: str | None, memory_project_key: str | None) -> float:
    if thread_project_key is not None and memory_project_key == thread_project_key:
        return 1.0
    if memory_project_key is None:
        return 0.5
    return 0.0


def _location_feature(
    *,
    location_path: str | None,
    origin_path: str | None,
    thread_project_key: str | None,
    memory_project_key: str | None,
    half_life_hops: float,
) -> float | None:
    """Compute ADR-010 distance only when workspace identity is provable."""

    if location_path is None or origin_path is None:
        return None
    if (
        thread_project_key is None
        or memory_project_key is None
        or thread_project_key != memory_project_key
    ):
        return 0.0 if thread_project_key is not None and memory_project_key is not None else None
    origin = origin_path
    if origin == memory_project_key:
        origin = "."
    elif origin.startswith(f"{memory_project_key}/"):
        origin = origin[len(memory_project_key) + 1 :]
    current_parts = _location_parts(location_path)
    origin_parts = _location_parts(origin)
    common = 0
    for current, learned in zip(current_parts, origin_parts, strict=False):
        if current != learned:
            break
        common += 1
    hops = len(current_parts) + len(origin_parts) - 2 * common
    return 2.0 ** (-hops / half_life_hops)


def _thread_feature(*, thread_id: UUID | None, origin_thread_id: UUID | None) -> float | None:
    """Return exact conversation locality, omitting legacy/non-thread births."""

    if thread_id is None or origin_thread_id is None:
        return None
    return 1.0 if origin_thread_id == thread_id else 0.0


def _where_feature(*, current_location: str | None, origin_location: str | None) -> float | None:
    """Score canonical folder proximity without penalizing unstamped history."""

    if current_location is None or origin_location is None:
        return None
    current = PurePosixPath(current_location)
    origin = PurePosixPath(origin_location)
    if (
        not current.is_absolute()
        or not origin.is_absolute()
        or ".." in current.parts
        or ".." in origin.parts
        or str(current) != current_location
        or str(origin) != origin_location
    ):
        raise ValueError("origin locations must be canonical absolute paths")
    if current == origin:
        return 1.0
    if current.is_relative_to(origin) or origin.is_relative_to(current):
        distance = abs(len(current.parts) - len(origin.parts))
        return 0.75 + 0.25 * (2.0 ** (-distance))
    if current.parent == origin.parent:
        return 0.5
    return 0.0


def _location_parts(value: str) -> tuple[str, ...]:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("location paths must be workspace-relative")
    return tuple(part for part in path.parts if part not in {".", ""})


def _citations(stats: Mapping[str, Any]) -> float:
    value = stats.get("citations", 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("stats.citations must be numeric")
    normalized = float(value)
    if normalized < 0.0 or not math.isfinite(normalized):
        raise ValueError("stats.citations must be a finite non-negative number")
    return normalized


def _validated_vector(values: Sequence[float], *, name: str) -> tuple[float, ...]:
    vector: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must contain only numbers")
        normalized = float(value)
        if not math.isfinite(normalized):
            raise ValueError(f"{name} must contain only finite numbers")
        vector.append(normalized)
    if not vector:
        raise ValueError(f"{name} must not be empty")
    if math.fsum(value * value for value in vector) == 0.0:
        raise ValueError(f"{name} must not have zero norm")
    return tuple(vector)


def _postgres_real(value: float) -> float:
    """Quantize score decisions once to C.2's IEEE-754 REAL width."""

    return struct.unpack("!f", struct.pack("!f", value))[0]


def _number(mapping: Mapping[str, Any], key: str) -> float:
    value = mapping[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"scorer config {key} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"scorer config {key} must be finite")
    return normalized


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"scorer {name} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"scorer {name} must be finite")
    return normalized


def _integer(mapping: Mapping[str, Any], key: str) -> int:
    value = mapping[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"scorer config {key} must be an integer")
    return value


def _optional_number(mapping: Mapping[str, Any], key: str, default: float) -> float:
    return _finite_number(mapping.get(key, default), key)


__all__ = [
    "DEFAULT_MEMORY_CONTEXT_SHARE",
    "MAX_MEMORY_CONTEXT_SHARE",
    "MIN_MEMORY_CONTEXT_SHARE",
    "ScoreFeatures",
    "ScoredCandidate",
    "ScorerConfig",
    "ScorerParams",
    "ScorerWeights",
    "ScoringCandidate",
    "ScoringSelection",
    "prompt_keywords",
    "score_and_select",
]
