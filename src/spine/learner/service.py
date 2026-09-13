"""Database boundary for reproducible M2F scorer proposals."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spine.db.locking import session_advisory_lock
from spine.db.models import (
    InjectionEvent,
    InjectionEventAnnotation,
    LearnerRun,
    OptimizationRun,
)
from spine.db.models import ScorerConfig as ScorerConfigRow
from spine.ids import mint_ulid
from spine.inject.scorer import ScorerConfig as RuntimeScorerConfig
from spine.inject.scorer import ScorerWeights
from spine.learner.contracts import ReplayScoreView, RetrainResponse
from spine.learner.evidence import LearnerDataError, LearningEvidence, project_learning_evidence
from spine.learner.locking import LEARNER_ADVISORY_LOCK_KEY
from spine.learner.model import (
    FEATURE_NAMES,
    FitSettings,
    LearningExample,
    ReplayScore,
    ShareBoundary,
    canonical_digest,
    challenger_score,
    challenger_wins,
    fit_pairwise,
    recorded_score,
    split_gates,
)

_ALGORITHM_ID = "m3ms-unified-share-tau-pairwise-v3"
_SHARE_TUNING_MINIMUM = 100


@dataclass(frozen=True, slots=True, kw_only=True)
class LearnerSettings:
    min_dispositions: int
    holdout_fraction: float
    passive_discount: float
    pair_margin: float
    bias_l2: float
    win_margin: float

    def manifest(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True, kw_only=True)
class OptimizationTrigger:
    """One event that caused an optimization check or run."""

    event_uid: str
    thread_id: UUID | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class _BalancedCorpus:
    examples: tuple[LearningExample, ...]
    share_boundaries: tuple[ShareBoundary, ...]
    fingerprint: str
    stratification: dict[str, object]


@dataclass(frozen=True, slots=True, kw_only=True)
class _OptimizationOutcome:
    response: RetrainResponse
    incumbent_params: dict[str, object]
    challenger_params: dict[str, object] | None
    backtest_scores: dict[str, object]
    tie_break_applied: dict[str, object]


class LearnerService:
    """Fit challengers from one database snapshot and persist winners inactive."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        settings: LearnerSettings,
        retrain_signal_stride: int = 25,
        corpus_max_dispositions: int = 1000,
    ) -> None:
        if retrain_signal_stride <= 0:
            raise ValueError("retrain signal stride must be positive")
        if corpus_max_dispositions < settings.min_dispositions:
            raise ValueError("optimization corpus max must cover the disposition floor")
        self._session_factory = session_factory
        self._settings = settings
        self._retrain_signal_stride = retrain_signal_stride
        self._corpus_max_dispositions = corpus_max_dispositions

    async def retrain(
        self,
        *,
        optimization_trigger: OptimizationTrigger | None = None,
    ) -> RetrainResponse:
        """Force the same fit used by background cadence and append its receipt."""

        response = await self._retrain(
            trigger="manual",
            due_only=False,
            optimization_trigger=optimization_trigger or OptimizationTrigger(event_uid=mint_ulid()),
        )
        if response is None:  # pragma: no cover - manual is never a due check
            raise RuntimeError("manual retrain unexpectedly returned no result")
        return response

    async def retrain_if_due(
        self,
        *,
        optimization_trigger: OptimizationTrigger | None = None,
    ) -> RetrainResponse | None:
        """Run one background fit only when the durable A-051 cadence is due."""

        return await self._retrain(
            trigger="background",
            due_only=True,
            optimization_trigger=optimization_trigger or OptimizationTrigger(event_uid=mint_ulid()),
        )

    async def compact(self, trigger: OptimizationTrigger) -> RetrainResponse:
        """D.2 144/153: main-thread compaction replaces the interim signal stride."""
        return await self._retrain(
            trigger="background", due_only=False, optimization_trigger=trigger,
        )

    async def _retrain(
        self,
        *,
        trigger: Literal["manual", "background"],
        due_only: bool,
        optimization_trigger: OptimizationTrigger,
    ) -> RetrainResponse | None:
        async with session_advisory_lock(
            self._session_factory,
            key=LEARNER_ADVISORY_LOCK_KEY,
            name="chrysopoeia",
        ) as connection:
            async with self._session_factory(bind=connection) as session:
                async with session.begin():
                    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
                    started_at = await _database_clock(session)
                    return await self._retrain_in_snapshot(
                        session,
                        trigger=trigger,
                        due_only=due_only,
                        optimization_trigger=optimization_trigger,
                        started_at=started_at,
                    )

    async def _retrain_in_snapshot(
        self,
        session: AsyncSession,
        *,
        trigger: Literal["manual", "background"],
        due_only: bool,
        optimization_trigger: OptimizationTrigger,
        started_at: datetime,
    ) -> RetrainResponse | None:
        configs = (await session.execute(select(ScorerConfigRow))).scalars().all()
        active_rows = [row for row in configs if row.active]
        if len(active_rows) != 1:
            raise LearnerDataError(
                f"expected exactly one active scorer_config row; found {len(active_rows)}"
            )
        active_row = active_rows[0]
        runtime_configs = {row.version: _runtime_config(row) for row in configs}
        incumbent = runtime_configs[active_row.version]
        event_rows = (
            (
                await session.execute(
                    select(InjectionEvent).order_by(
                        InjectionEvent.ts,
                        InjectionEvent.injection_id,
                        InjectionEvent.event_uid,
                    )
                )
            )
            .scalars()
            .all()
        )
        annotation_rows = (await session.execute(select(InjectionEventAnnotation))).scalars().all()
        evidence = self._evidence(event_rows, annotation_rows, runtime_configs)
        eligible_dispositions = len(evidence.examples)
        if due_only and not await self._background_due(session, eligible_dispositions):
            return None
        corpus = _balanced_corpus(
            evidence.examples,
            evidence.share_boundaries,
            max_dispositions=self._corpus_max_dispositions,
        )
        outcome = await self._fit(
            session,
            configs=configs,
            active_row=active_row,
            incumbent=incumbent,
            eligible_dispositions=eligible_dispositions,
            corpus=corpus,
        )
        # The mappings intentionally expose no ORM relationship. Persist a
        # winning proposal before its same-transaction receipt satisfies the FK.
        await session.flush()
        source_boundary = max(
            (example.event_uid for example in evidence.examples),
            default=None,
        )
        run_uid = mint_ulid()
        response = outcome.response
        session.add(
            LearnerRun(
                run_uid=run_uid,
                trigger=trigger,
                result=response.status,
                incumbent_version=response.incumbent_version,
                proposal_version=response.proposal_version,
                eligible_dispositions=response.eligible_dispositions,
                training_dispositions=response.training_dispositions,
                holdout_dispositions=response.holdout_dispositions,
                training_pairs=response.training_pairs,
                source_boundary=source_boundary,
                incumbent=(
                    response.incumbent.model_dump() if response.incumbent is not None else None
                ),
                challenger=(
                    response.challenger.model_dump() if response.challenger is not None else None
                ),
                reason=response.reason,
            )
        )
        completed_at = await _database_clock(session)
        session.add(
            OptimizationRun(
                run_uid=run_uid,
                loop="injection_scoring",
                trigger_kind=trigger,
                trigger_event_uid=optimization_trigger.event_uid,
                trigger_thread_id=optimization_trigger.thread_id,
                corpus_fingerprint=corpus.fingerprint,
                corpus_size=len(corpus.examples),
                corpus_max_size=self._corpus_max_dispositions,
                corpus_stratification=corpus.stratification,
                incumbent_version=response.incumbent_version,
                challenger_version=response.proposal_version,
                incumbent_params=outcome.incumbent_params,
                challenger_params=outcome.challenger_params,
                backtest_scores=outcome.backtest_scores,
                verdict=response.status,
                tie_break_applied=outcome.tie_break_applied,
                cost_refs=[],
                started_at=started_at,
                completed_at=completed_at,
            )
        )
        return response

    async def _background_due(self, session: AsyncSession, eligible: int) -> bool:
        if eligible < self._settings.min_dispositions:
            return False
        cursor = await session.scalar(
            select(LearnerRun)
            .where(LearnerRun.eligible_dispositions >= self._settings.min_dispositions)
            .order_by(
                LearnerRun.eligible_dispositions.desc(),
                LearnerRun.ts.desc(),
                LearnerRun.run_uid.desc(),
            )
            .limit(1)
        )
        if cursor is None:
            return True
        return eligible - cursor.eligible_dispositions >= self._retrain_signal_stride

    async def _fit(
        self,
        session: AsyncSession,
        *,
        configs: list[ScorerConfigRow],
        active_row: ScorerConfigRow,
        incumbent: RuntimeScorerConfig,
        eligible_dispositions: int,
        corpus: _BalancedCorpus,
    ) -> _OptimizationOutcome:
        examples = corpus.examples
        share_boundaries = corpus.share_boundaries
        prior_challenger_exists = any(
            isinstance(row.params.get("_learner"), Mapping) for row in configs
        )
        if not prior_challenger_exists and eligible_dispositions < self._settings.min_dispositions:
            return _insufficient_outcome(
                active_row,
                eligible=eligible_dispositions,
                reason=(
                    "minimum disposition floor not reached: "
                    f"{eligible_dispositions}/{self._settings.min_dispositions}"
                ),
            )
        try:
            training, holdout, cutoff = split_gates(
                examples,
                holdout_fraction=self._settings.holdout_fraction,
            )
        except ValueError as error:
            return _insufficient_outcome(
                active_row,
                eligible=eligible_dispositions,
                reason=str(error),
            )
        training_ids = {example.injection_id for example in training}
        holdout_ids = {example.injection_id for example in holdout}
        training_boundaries = tuple(
            boundary for boundary in share_boundaries if boundary.injection_id in training_ids
        )
        holdout_boundaries = tuple(
            boundary for boundary in share_boundaries if boundary.injection_id in holdout_ids
        )
        tune_share_and_tau = len(examples) >= _SHARE_TUNING_MINIMUM
        try:
            fit = fit_pairwise(
                training,
                incumbent_weights=_weight_tuple(incumbent.weights),
                incumbent_thread_weight=incumbent.params.thread_weight,
                incumbent_where_weight=incumbent.params.where_weight,
                incumbent_tau=incumbent.params.tau,
                incumbent_memory_context_share=incumbent.params.memory_context_share,
                share_boundaries=training_boundaries,
                tune_share_and_tau=tune_share_and_tau,
                settings=FitSettings(
                    pair_margin=self._settings.pair_margin,
                    bias_l2=self._settings.bias_l2,
                ),
            )
        except ValueError as error:
            return _insufficient_outcome(
                active_row,
                eligible=eligible_dispositions,
                training=len(training),
                holdout=len(holdout),
                reason=str(error),
            )
        incumbent_training_score = recorded_score(
            training,
            share_boundaries=training_boundaries,
            memory_context_share=incumbent.params.memory_context_share,
        )
        fitted_training_score = challenger_score(
            training,
            weights=fit.weights,
            bias_offsets=fit.bias_offsets,
            thread_weight=fit.thread_weight,
            where_weight=fit.where_weight,
            tau=fit.tau,
            share_boundaries=training_boundaries,
            memory_context_share=fit.memory_context_share,
        )
        incumbent_score = recorded_score(
            holdout,
            share_boundaries=holdout_boundaries,
            memory_context_share=incumbent.params.memory_context_share,
        )
        fitted_score = challenger_score(
            holdout,
            weights=fit.weights,
            bias_offsets=fit.bias_offsets,
            thread_weight=fit.thread_weight,
            where_weight=fit.where_weight,
            tau=fit.tau,
            share_boundaries=holdout_boundaries,
            memory_context_share=fit.memory_context_share,
        )
        wins = challenger_wins(
            incumbent_score,
            fitted_score,
            margin=Decimal(str(self._settings.win_margin)),
            incumbent_memory_context_share=incumbent.params.memory_context_share,
            challenger_memory_context_share=fit.memory_context_share,
            incumbent_tau=incumbent.params.tau,
            challenger_tau=fit.tau,
        )
        proposal_weights = dict(zip(FEATURE_NAMES, fit.weights, strict=True))
        evaluated_params = deepcopy(active_row.params)
        evaluated_params.pop("_control", None)
        evaluated_params.pop("_learner", None)
        if "thread_weight" in evaluated_params or fit.thread_weight != 0.0:
            evaluated_params["thread_weight"] = fit.thread_weight
        if "where_weight" in evaluated_params or fit.where_weight != 0.0:
            evaluated_params["where_weight"] = fit.where_weight
        if fit.share_tau_active:
            evaluated_params["tau"] = fit.tau
            evaluated_params["memory_context_share"] = fit.memory_context_share
        evaluated_params["bias_offsets"] = {
            str(memory_id): value
            for memory_id, value in sorted(fit.bias_offsets.items(), key=lambda item: item[0].int)
        }
        backtest_scores = {
            "train": {
                "incumbent": _score_manifest(incumbent_training_score),
                "challenger": _score_manifest(fitted_training_score),
            },
            "holdout": {
                "incumbent": _score_manifest(incumbent_score),
                "challenger": _score_manifest(fitted_score),
            },
        }
        tie_break_applied = _tie_break_manifest(
            incumbent_score,
            fitted_score,
            margin=Decimal(str(self._settings.win_margin)),
            incumbent_share=incumbent.params.memory_context_share,
            challenger_share=fit.memory_context_share,
            incumbent_tau=incumbent.params.tau,
            challenger_tau=fit.tau,
            winner=wins,
        )
        if not wins:
            return _OptimizationOutcome(
                response=RetrainResponse(
                    status="not_better",
                    incumbent_version=incumbent.version,
                    proposal_version=None,
                    eligible_dispositions=eligible_dispositions,
                    training_dispositions=len(training),
                    holdout_dispositions=len(holdout),
                    training_pairs=fit.pair_count,
                    incumbent=_score_view(incumbent_score),
                    challenger=_score_view(fitted_score),
                    reason="challenger did not clear the replay win rule",
                ),
                incumbent_params=_config_parameter_manifest(active_row),
                challenger_params={
                    "version": None,
                    "weights": proposal_weights,
                    "params": evaluated_params,
                },
                backtest_scores=backtest_scores,
                tie_break_applied=tie_break_applied,
            )
        settings_manifest = {
            **self._settings.manifest(),
            "corpus_max_dispositions": self._corpus_max_dispositions,
        }
        proposal_params = deepcopy(active_row.params)
        inherited_control = "_control" in proposal_params
        proposal_params.pop("_control", None)
        digest_manifest = {
            "algorithm": _ALGORITHM_ID,
            "learner": settings_manifest,
            "incumbent_weights": active_row.weights,
            "incumbent_params": active_row.params,
            "corpus_fingerprint": corpus.fingerprint,
            "corpus_strategy": corpus.stratification["strategy"],
        }
        if inherited_control:
            # Pre-fix control-basin proposals inherited _control at the same digest.
            # Version the corrected encoding without erasing incumbent provenance.
            digest_manifest["proposal_encoding"] = "learner_without_control_v1"
        digest = canonical_digest(
            incumbent_version=incumbent.version,
            training=training,
            holdout=holdout,
            share_boundaries=training_boundaries,
            settings=digest_manifest,
        )
        version = f"m2f-{digest[:16]}"
        holdout_weight = sum(
            (example.actor_weight for example in holdout),
            start=Decimal(0),
        )
        proposal_params["_learner"] = {
            "status": "proposed",
            "algorithm": _ALGORITHM_ID,
            "source_digest": digest,
            "source_boundary": max(example.event_uid for example in examples),
            "training_cutoff": cutoff.isoformat(),
            "holdout_dispositions": len(holdout),
            "holdout_weight": str(holdout_weight),
            "settings": settings_manifest,
            "fit": {
                "iterations": fit.iterations,
                "objective": fit.objective,
                "training_pairs": fit.pair_count,
                "thread_weight": fit.thread_weight,
                "where_weight": fit.where_weight,
                "tau": fit.tau,
                "memory_context_share": fit.memory_context_share,
                "share_tau_active": fit.share_tau_active,
            },
            "replay": {
                "incumbent": _score_manifest(incumbent_score),
                "challenger": _score_manifest(fitted_score),
            },
            "bias_offsets": {
                str(memory_id): value
                for memory_id, value in sorted(
                    fit.bias_offsets.items(), key=lambda item: item[0].int
                )
            },
        }
        if "thread_weight" in proposal_params or fit.thread_weight != 0.0:
            proposal_params["thread_weight"] = fit.thread_weight
        if "where_weight" in proposal_params or fit.where_weight != 0.0:
            proposal_params["where_weight"] = fit.where_weight
        if fit.share_tau_active:
            proposal_params["tau"] = fit.tau
            proposal_params["memory_context_share"] = fit.memory_context_share
        existing = await session.get(ScorerConfigRow, version)
        if existing is None:
            session.add(
                ScorerConfigRow(
                    version=version,
                    weights=proposal_weights,
                    params=proposal_params,
                    active=False,
                )
            )
        elif existing.weights != proposal_weights or existing.params != proposal_params:
            legacy_with_count = deepcopy(proposal_params)
            legacy_learner = legacy_with_count.get("_learner")
            if isinstance(legacy_learner, dict):
                legacy_learner.pop("holdout_weight", None)
            legacy_without_count = deepcopy(legacy_with_count)
            legacy_learner = legacy_without_count.get("_learner")
            if isinstance(legacy_learner, dict):
                legacy_learner.pop("holdout_dispositions", None)
            accepted_params = (legacy_with_count, legacy_without_count)
            if existing.weights != proposal_weights or existing.params not in accepted_params:
                raise LearnerDataError(
                    f"content-addressed scorer proposal {version} does not match stored content"
                )
        return _OptimizationOutcome(
            response=RetrainResponse(
                status="proposed",
                incumbent_version=incumbent.version,
                proposal_version=version,
                eligible_dispositions=eligible_dispositions,
                training_dispositions=len(training),
                holdout_dispositions=len(holdout),
                training_pairs=fit.pair_count,
                incumbent=_score_view(incumbent_score),
                challenger=_score_view(fitted_score),
                reason="challenger won replay and remains inactive pending owner activation",
            ),
            incumbent_params=_config_parameter_manifest(active_row),
            challenger_params={
                "version": version,
                "weights": proposal_weights,
                "params": proposal_params,
            },
            backtest_scores=backtest_scores,
            tie_break_applied=tie_break_applied,
        )

    def _evidence(
        self,
        rows: list[InjectionEvent],
        annotations: list[InjectionEventAnnotation],
        configs: Mapping[str, RuntimeScorerConfig],
    ) -> LearningEvidence:
        return project_learning_evidence(
            rows,
            annotations,
            configs,
            passive_discount=Decimal(str(self._settings.passive_discount)),
        )


async def _database_clock(session: AsyncSession) -> datetime:
    value = await session.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise LearnerDataError("Postgres returned an invalid optimization timestamp")
    return value


def _balanced_corpus(
    examples: Sequence[LearningExample],
    share_boundaries: Sequence[ShareBoundary],
    *,
    max_dispositions: int,
) -> _BalancedCorpus:
    """Cap whole gates while round-robining threads across old and new history."""

    gates: dict[UUID, list[LearningExample]] = defaultdict(list)
    for example in examples:
        gates[example.injection_id].append(example)
    by_thread: dict[str, list[tuple[LearningExample, ...]]] = defaultdict(list)
    for injection_id, rows in gates.items():
        thread_ids = {row.thread_id for row in rows}
        if len(thread_ids) != 1:
            raise LearnerDataError(f"injection {injection_id} crosses thread strata")
        if len(rows) > max_dispositions:
            raise LearnerDataError(
                f"injection {injection_id} exceeds the optimization corpus maximum"
            )
        ordered = tuple(sorted(rows, key=lambda row: (row.ts, row.event_uid)))
        thread_id = next(iter(thread_ids))
        by_thread[str(thread_id) if thread_id is not None else "not_recorded"].append(ordered)

    queues: dict[str, deque[tuple[LearningExample, ...]]] = {}
    for thread_id, thread_gates in by_thread.items():
        chronological = sorted(
            thread_gates,
            key=lambda rows: (max(row.ts for row in rows), rows[0].injection_id.int),
        )
        spread: list[tuple[LearningExample, ...]] = []
        history = deque(chronological)
        newest = True
        while history:
            spread.append(history.pop() if newest else history.popleft())
            newest = not newest
        queues[thread_id] = deque(spread)

    selected: list[LearningExample] = []
    selected_by_thread: dict[str, int] = defaultdict(int)
    selected_gates_by_thread: dict[str, int] = defaultdict(int)
    remaining = max_dispositions
    thread_order = sorted(queues)
    while remaining and any(queues[thread_id] for thread_id in thread_order):
        progress = False
        for thread_id in thread_order:
            queue = queues[thread_id]
            if not queue:
                continue
            gate = queue[0]
            if len(gate) > remaining:
                continue
            queue.popleft()
            selected.extend(gate)
            selected_by_thread[thread_id] += len(gate)
            selected_gates_by_thread[thread_id] += 1
            remaining -= len(gate)
            progress = True
            if remaining == 0:
                break
        if not progress:
            break

    selected.sort(key=lambda row: (row.ts, row.injection_id.int, row.event_uid))
    selected_injections = {row.injection_id for row in selected}
    selected_boundaries = tuple(
        boundary for boundary in share_boundaries if boundary.injection_id in selected_injections
    )
    fingerprint_payload = {
        "dispositions": [row.event_uid for row in selected],
        "share_boundaries": sorted(boundary.event_uid for boundary in selected_boundaries),
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    available_by_thread = {
        thread_id: sum(len(gate) for gate in thread_gates)
        for thread_id, thread_gates in by_thread.items()
    }
    stratification = {
        "strategy": "thread_round_robin_newest_oldest_v1",
        "unit": "eligible_disposition",
        "available": len(examples),
        "selected": len(selected),
        "max_size": max_dispositions,
        "threads": [
            {
                "thread_id": thread_id,
                "available": available_by_thread[thread_id],
                "selected": selected_by_thread[thread_id],
                "selected_gates": selected_gates_by_thread[thread_id],
            }
            for thread_id in sorted(by_thread)
        ],
    }
    return _BalancedCorpus(
        examples=tuple(selected),
        share_boundaries=selected_boundaries,
        fingerprint=fingerprint,
        stratification=stratification,
    )


def _runtime_config(row: ScorerConfigRow) -> RuntimeScorerConfig:
    try:
        return RuntimeScorerConfig.from_mappings(
            version=row.version,
            weights=row.weights,
            params=row.params,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise LearnerDataError(f"scorer_config {row.version!r} is invalid") from error


def _weight_tuple(weights: ScorerWeights) -> tuple[float, float, float, float, float, float]:
    return (weights.sem, weights.kw, weights.time, weights.proj, weights.freq, weights.hist)


def _score_view(score: ReplayScore) -> ReplayScoreView:
    return ReplayScoreView(**_score_manifest(score))


def _score_manifest(score: ReplayScore) -> dict[str, Any]:
    return {
        "disagreements": score.disagreements,
        "weighted_disagreements": str(score.weighted_disagreements),
        "injected_tokens": score.injected_tokens,
        "share_disagreements": score.share_disagreements,
        "weighted_share_disagreements": str(score.weighted_share_disagreements),
    }


def _config_parameter_manifest(row: ScorerConfigRow) -> dict[str, object]:
    return {
        "version": row.version,
        "weights": deepcopy(row.weights),
        "params": deepcopy(row.params),
    }


def _tie_break_manifest(
    incumbent: ReplayScore,
    challenger: ReplayScore,
    *,
    margin: Decimal,
    incumbent_share: float,
    challenger_share: float,
    incumbent_tau: float,
    challenger_tau: float,
    winner: bool,
) -> dict[str, object]:
    improvement = incumbent.weighted_disagreements - challenger.weighted_disagreements
    if improvement == 0:
        return {
            "applied": True,
            "rule": "cheaper_at_exact_tie",
            "winner": "challenger" if winner else "incumbent",
            "incumbent_cost": {
                "injected_tokens": incumbent.injected_tokens,
                "memory_context_share": incumbent_share,
                "tau": incumbent_tau,
            },
            "challenger_cost": {
                "injected_tokens": challenger.injected_tokens,
                "memory_context_share": challenger_share,
                "tau": challenger_tau,
            },
        }
    return {
        "applied": False,
        "rule": "replay_margin",
        "winner": "challenger" if winner else "incumbent",
        "weighted_improvement": str(improvement),
        "required_margin": str(margin),
    }


def _insufficient_outcome(
    incumbent: ScorerConfigRow,
    *,
    eligible: int,
    reason: str,
    training: int = 0,
    holdout: int = 0,
) -> _OptimizationOutcome:
    return _OptimizationOutcome(
        response=_insufficient(
            incumbent.version,
            eligible=eligible,
            reason=reason,
            training=training,
            holdout=holdout,
        ),
        incumbent_params=_config_parameter_manifest(incumbent),
        challenger_params=None,
        backtest_scores={
            "train": {"status": "not_scored", "reason": reason},
            "holdout": {"status": "not_scored", "reason": reason},
        },
        tie_break_applied={
            "applied": False,
            "rule": "not_scored",
            "reason": reason,
        },
    )


def _insufficient(
    incumbent_version: str,
    *,
    eligible: int,
    reason: str,
    training: int = 0,
    holdout: int = 0,
) -> RetrainResponse:
    return RetrainResponse(
        status="insufficient_data",
        incumbent_version=incumbent_version,
        proposal_version=None,
        eligible_dispositions=eligible,
        training_dispositions=training,
        holdout_dispositions=holdout,
        training_pairs=0,
        incumbent=None,
        challenger=None,
        reason=reason,
    )


__all__ = [
    "LearnerDataError",
    "LearnerService",
    "LearnerSettings",
    "OptimizationTrigger",
]
