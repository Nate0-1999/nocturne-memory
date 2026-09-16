"""Database authority for the A-035 Memory Graph and Injection Console."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from spine.contracts import MemoryFeatures, MemoryUnit
from spine.db.locking import session_advisory_lock
from spine.db.models import (
    InjectionEvent,
    InjectionEventAnnotation,
    LearnerRun,
    OptimizationRun,
    OptimizationRunAdoption,
)
from spine.db.models import MemoryEdge as MemoryEdgeRow
from spine.db.models import MemoryRevision as MemoryRevisionRow
from spine.db.models import MemoryUnit as MemoryUnitRow
from spine.db.models import ScorerActivation as ScorerActivationRow
from spine.db.models import ScorerConfig as ScorerConfigRow
from spine.inject.scorer import (
    DEFAULT_MEMORY_CONTEXT_SHARE,
    MAX_MEMORY_CONTEXT_SHARE,
    MIN_MEMORY_CONTEXT_SHARE,
    score_features,
)
from spine.inject.scorer import ScorerConfig as RuntimeScorerConfig
from spine.learner.creation import creation_snapshot
from spine.learner.evidence import LearnerDataError, project_learning_evidence
from spine.learner.model import (
    FEATURE_NAMES,
    LearningExample,
    challenger_score,
    split_gates,
)
from spine.learner.service import LearnerService
from spine.m2k.contracts import (
    AccuracyPoint,
    AccuracySlice,
    AccuracySlicePoint,
    ActivateScorerConfigRequest,
    CandidateScoreHistory,
    CandidateScorePoint,
    ContributionBreakdown,
    CreateScorerConfigRequest,
    InstantSimulation,
    LearnerRunView,
    LearningAnnotation,
    LearningView,
    LiveAgreementPoint,
    MemoryGraphEdge,
    MemoryGraphNode,
    MemoryGraphQuery,
    MemoryGraphSnapshot,
    ParameterRange,
    RevisionTrailItem,
    ScorerActivationView,
    ScorerAuditionRequest,
    ScorerAuditionResponse,
    ScorerComparisonRow,
    ScorerConfigurationView,
    ScorerConsoleQuery,
    ScorerConsoleSnapshot,
    ScorerDescriptor,
    ScorerSimulationRequest,
    ScorerSimulationResponse,
    ScorerValues,
)
from spine.tokens import cl100k_token_count

_CONTROL_ADVISORY_LOCK_KEY = 0x4D324B
_CONTROLLED_PARAM_KEYS = (
    "tau",
    "top_k",
    "memory_context_share",
    "half_life_time_days",
    "half_life_hist_days",
)

SCORER_DESCRIPTORS = (
    ScorerDescriptor(
        id="scorer.tau",
        label="Injection threshold",
        type="number",
        range=ParameterRange(minimum=0, maximum=1, step=0.01),
        default=0.55,
    ),
    ScorerDescriptor(
        id="scorer.memory_context_share",
        label="Memory context share",
        type="number",
        range=ParameterRange(
            minimum=MIN_MEMORY_CONTEXT_SHARE,
            maximum=MAX_MEMORY_CONTEXT_SHARE,
            step=0.01,
        ),
        default=DEFAULT_MEMORY_CONTEXT_SHARE,
    ),
    ScorerDescriptor(
        id="scorer.half_life_time_days",
        label="Recency half-life (days)",
        type="number",
        range=ParameterRange(minimum=0, maximum=None, step=0.5, exclusive_minimum=True),
        default=14,
    ),
    ScorerDescriptor(
        id="scorer.half_life_hist_days",
        label="Edit-history half-life (days)",
        type="number",
        range=ParameterRange(minimum=0, maximum=None, step=0.5, exclusive_minimum=True),
        default=7,
    ),
    *(
        ScorerDescriptor(
            id=f"scorer.weight.{feature}",
            label=f"{feature.upper()} weight",
            type="number",
            range=ParameterRange(minimum=0, maximum=1, step=0.01),
            default=default,
        )
        for feature, default in zip(
            FEATURE_NAMES, (0.42, 0.16, 0.11, 0.16, 0.08, 0.07), strict=True
        )
    ),
)


class M2KStateError(RuntimeError):
    """The requested graph or scorer operation cannot preserve A-035."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class M2KService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        graph_edge_sim: float,
        holdout_fraction: float = 0.20,
        passive_discount: float = 0.25,
        learner_min_dispositions: int = 25,
        retrain_signal_stride: int = 25,
    ) -> None:
        if learner_min_dispositions <= 0 or retrain_signal_stride <= 0:
            raise ValueError("learner floor and retrain stride must be positive")
        self._session_factory = session_factory
        self._graph_edge_sim = graph_edge_sim
        self._holdout_fraction = holdout_fraction
        self._passive_discount = passive_discount
        self._learner_min_dispositions = learner_min_dispositions
        self._share_tuning_minimum = 100
        self._retrain_signal_stride = retrain_signal_stride

    async def memory_graph(self, query: MemoryGraphQuery) -> MemoryGraphSnapshot:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                )
                as_of = await _transaction_time(session)
                statement = select(MemoryUnitRow).where(
                    MemoryUnitRow.principal_id == query.principal_id,
                    MemoryUnitRow.status.not_in(("candidate", "staged")),
                )
                if query.memory_ids is not None:
                    statement = statement.where(MemoryUnitRow.id.in_(query.memory_ids))
                rows = (
                    (
                        await session.execute(
                            statement.order_by(MemoryUnitRow.created_at, MemoryUnitRow.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                node_ids = [row.id for row in rows]
                revisions = await _revision_map(session, node_ids)
                edges = await self._graph_edges(session, node_ids, revisions)

        returned = set(node_ids)
        requested = query.memory_ids or []
        return MemoryGraphSnapshot(
            as_of=as_of,
            graph_edge_sim=self._graph_edge_sim,
            nodes=[
                MemoryGraphNode(
                    memory=_memory_unit(row),
                    in_current_context=query.memory_ids is not None,
                    revisions=revisions.get(row.id, []),
                )
                for row in rows
            ],
            edges=edges,
            omitted_memory_ids=[memory_id for memory_id in requested if memory_id not in returned],
        )

    async def _graph_edges(
        self,
        session: AsyncSession,
        node_ids: list[UUID],
        revisions: Mapping[UUID, list[RevisionTrailItem]],
    ) -> list[MemoryGraphEdge]:
        if not node_ids:
            return []
        left = aliased(MemoryUnitRow)
        right = aliased(MemoryUnitRow)
        similarity = (1 - left.embedding.cosine_distance(right.embedding)).label("similarity")
        similarity_rows = (
            await session.execute(
                select(left.id, right.id, similarity)
                .where(
                    left.id.in_(node_ids),
                    right.id.in_(node_ids),
                    left.id < right.id,
                    similarity >= self._graph_edge_sim,
                )
                .order_by(left.id, right.id)
            )
        ).all()
        lineage_rows = (
            (
                await session.execute(
                    select(MemoryEdgeRow)
                    .where(
                        MemoryEdgeRow.from_memory_id.in_(node_ids),
                        MemoryEdgeRow.to_memory_id.in_(node_ids),
                    )
                    .order_by(
                        MemoryEdgeRow.created_at,
                        MemoryEdgeRow.edge_uid,
                    )
                )
            )
            .scalars()
            .all()
        )
        edges = [
            MemoryGraphEdge(
                kind="similarity",
                from_memory_id=from_id,
                to_memory_id=to_id,
                similarity=_decimal_string(sim),
            )
            for from_id, to_id, sim in similarity_rows
        ]
        edges.extend(
            MemoryGraphEdge(
                kind="lineage",
                from_memory_id=row.from_memory_id,
                to_memory_id=row.to_memory_id,
                edge_type=row.edge_type,  # type: ignore[arg-type]
            )
            for row in lineage_rows
        )
        edges.extend(
            MemoryGraphEdge(
                kind="edit_trail",
                from_memory_id=memory_id,
                to_memory_id=memory_id,
                revision_count=len(trail),
            )
            for memory_id, trail in sorted(revisions.items(), key=lambda item: item[0].int)
            if len(trail) > 1
        )
        return edges

    async def scorer_console(
        self, query: ScorerConsoleQuery, *, palace_scope: bool = False
    ) -> ScorerConsoleSnapshot:
        if query.as_of != "now":
            raise M2KStateError("historical_unavailable")
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                )
                as_of = await _transaction_time(session)
                creation = await creation_snapshot(
                    session, None if palace_scope else query.principal_id
                )
                configs = (
                    (
                        await session.execute(
                            select(ScorerConfigRow).order_by(
                                ScorerConfigRow.created_at, ScorerConfigRow.version
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                active = [row for row in configs if row.active]
                if len(active) != 1:
                    raise M2KStateError("invalid_active_scorer")
                activations = (
                    []
                    if not palace_scope
                    else (
                        (
                            await session.execute(
                                select(ScorerActivationRow).order_by(
                                    ScorerActivationRow.ts,
                                    ScorerActivationRow.event_uid,
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                )
                retrain_runs = (
                    []
                    if not palace_scope
                    else (
                        (
                            await session.execute(
                                select(LearnerRun).order_by(LearnerRun.ts, LearnerRun.run_uid)
                            )
                        )
                        .scalars()
                        .all()
                    )
                )
                learning_events = (
                    (
                        await session.execute(
                            select(InjectionEvent)
                            .where(
                                *(
                                    []
                                    if palace_scope
                                    else [InjectionEvent.principal_id == query.principal_id]
                                )
                            )
                            .order_by(
                                InjectionEvent.ts,
                                InjectionEvent.injection_id,
                                InjectionEvent.event_uid,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                learning_annotations = (
                    (
                        await session.execute(
                            select(InjectionEventAnnotation).where(
                                *(
                                    []
                                    if palace_scope
                                    else [
                                        InjectionEventAnnotation.target_principal_id
                                        == query.principal_id
                                    ]
                                )
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                event_statement = select(InjectionEvent).where(
                    InjectionEvent.principal_id == query.principal_id
                )
                if query.thread_id is not None:
                    event_statement = event_statement.where(
                        InjectionEvent.thread_id == query.thread_id
                    )
                events = (
                    (
                        await session.execute(
                            event_statement.order_by(InjectionEvent.ts, InjectionEvent.event_uid)
                        )
                    )
                    .scalars()
                    .all()
                )

        config_views = [_config_view(row) for row in configs]
        if not palace_scope:
            # F100: expose the current scoring policy, never other principals' replay metrics.
            config_views = [
                view.model_copy(update={"replay": None})
                for view in config_views
                if view.status == "active"
            ]
        config_by_version = {row.version: row for row in configs}
        candidates = _candidate_histories(events, config_by_version)
        proposals = [view for view in config_views if view.status == "proposed"]
        try:
            evidence = project_learning_evidence(
                learning_events,
                learning_annotations,
                {version: _runtime(row) for version, row in config_by_version.items()},
                passive_discount=Decimal(str(self._passive_discount)),
            )
        except LearnerDataError as error:
            raise M2KStateError(f"invalid_learning_evidence:{error}") from error
        return ScorerConsoleSnapshot(
            creation=creation,
            trainables=LearnerService.manifest(
                len(evidence.examples), self._learner_min_dispositions
            ),
            as_of=as_of,
            metrics_scope="palace" if palace_scope else "principal",
            scope="CURRENT" if query.thread_id is not None else "GLOBAL",
            thread_id=query.thread_id,
            descriptors=list(SCORER_DESCRIPTORS),
            active_version=active[0].version,
            configurations=config_views,
            activations=[_activation_view(row) for row in activations],
            proposed_versions=proposals,
            accuracy=[_accuracy_point(row) for row in configs] if palace_scope else [],
            learning=_learning_view(
                evidence_examples=evidence.examples,
                hygiene_excluded=evidence.hygiene_excluded_dispositions,
                events=learning_events,
                active_version=active[0].version,
                runs=retrain_runs,
                activations=activations,
                minimum_dispositions=self._learner_min_dispositions,
                share_tuning_minimum=self._share_tuning_minimum,
                retrain_signal_stride=self._retrain_signal_stride,
            ),
            candidates=candidates,
        )

    async def create_scorer_config(
        self, body: CreateScorerConfigRequest
    ) -> ScorerConfigurationView:
        target_version = f"m2k-{body.event_uid}"
        async with session_advisory_lock(
            self._session_factory,
            key=_CONTROL_ADVISORY_LOCK_KEY,
            name="m2k-control",
        ) as connection:
            async with self._session_factory(bind=connection) as session:
                async with session.begin():
                    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
                    return await self._create_scorer_config_in_snapshot(
                        session,
                        body,
                        target_version,
                    )

    async def _create_scorer_config_in_snapshot(
        self,
        session: AsyncSession,
        body: CreateScorerConfigRequest,
        target_version: str,
    ) -> ScorerConfigurationView:
        replay = await session.get(ScorerActivationRow, body.event_uid)
        if replay is not None:
            return await _validate_control_replay(session, replay, target_version, body)
        active = await _active_config(session)
        if active.version != body.base_version:
            raise M2KStateError("stale_base")
        receipt = await self._deep_receipt(session, active, body.values)
        if receipt.simulation_digest != body.simulation_digest:
            raise M2KStateError("simulation_stale")
        if await session.get(ScorerConfigRow, target_version) is not None:
            raise M2KStateError("version_collision")
        params = deepcopy(active.params)
        params.update(
            {
                "tau": body.values.tau,
                "top_k": body.values.top_k,
                "memory_context_share": body.values.memory_context_share,
                "half_life_time_days": body.values.half_life_time_days,
                "half_life_hist_days": body.values.half_life_hist_days,
            }
        )
        changes = _changes(_values(active), body.values)
        changed_parameter_ids = sorted(changes)
        changes["_force"] = {
            "simulation_digest": receipt.simulation_digest,
            "source_boundary": receipt.source_boundary,
            "holdout_dispositions": receipt.holdout_dispositions,
            "incumbent_accuracy_percent": receipt.incumbent_accuracy_percent,
            "accuracy_percent": receipt.accuracy_percent,
            "delta_percent": receipt.delta_percent,
        }
        params["_control"] = {
            "event_uid": body.event_uid,
            "parent_version": active.version,
            "actor_class": body.actor_class,
            "machine_id": body.machine_id,
            "changed_parameter_ids": changed_parameter_ids,
        }
        _validate_runtime_config(target_version, body.values.weights, params)
        active.active = False
        await session.flush()
        target = ScorerConfigRow(
            version=target_version,
            weights=dict(body.values.weights),
            params=params,
            active=True,
        )
        session.add(target)
        await session.flush()
        session.add(
            ScorerActivationRow(
                event_uid=body.event_uid,
                version=target_version,
                previous_version=active.version,
                actor_class=body.actor_class,
                machine_id=body.machine_id,
                reason="human_control",
                changes=changes,
            )
        )
        await session.flush()
        await session.refresh(target)
        return _config_view(target)

    async def simulate(self, body: ScorerSimulationRequest) -> ScorerSimulationResponse:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                )
                base = await session.get(ScorerConfigRow, body.base_version)
                if base is None or not base.active:
                    raise M2KStateError("stale_base")
                receipt = await self._deep_receipt(session, base, body.values, include_terrain=True)
                instant = await _instant(
                    session,
                    principal_id=body.principal_id,
                    injection_id=body.injection_id,
                    preview=base,
                    preview_values=body.values,
                )
                slice_view = await self._slice(
                    session,
                    base,
                    body.values,
                    body.slice_parameter_id,
                )
        return receipt.model_copy(update={"instant": instant, "slice": slice_view})

    async def audition(self, body: ScorerAuditionRequest) -> ScorerAuditionResponse:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                )
                incumbent = await _active_config(session)
                proposal = await session.get(ScorerConfigRow, body.proposal_version)
                learner = proposal.params.get("_learner") if proposal is not None else None
                if (
                    proposal is None
                    or proposal.active
                    or not isinstance(learner, Mapping)
                    or learner.get("status") != "proposed"
                ):
                    raise M2KStateError("not_proposed")
                instant = await _instant(
                    session,
                    principal_id=body.principal_id,
                    injection_id=body.injection_id,
                    preview=proposal,
                    preview_values=_values(proposal),
                )
        return ScorerAuditionResponse(
            incumbent_version=incumbent.version,
            proposal_version=proposal.version,
            instant=instant,
        )

    async def _deep_receipt(
        self,
        session: AsyncSession,
        base: ScorerConfigRow,
        values: ScorerValues,
        *,
        include_terrain: bool = False,
    ) -> ScorerSimulationResponse:
        configs = (await session.execute(select(ScorerConfigRow))).scalars().all()
        config_map = {row.version: _runtime(row) for row in configs}
        rows = (
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
        annotations = (await session.execute(select(InjectionEventAnnotation))).scalars().all()
        try:
            evidence = project_learning_evidence(
                rows,
                annotations,
                config_map,
                passive_discount=Decimal(str(self._passive_discount)),
            )
        except LearnerDataError as error:
            raise M2KStateError(f"invalid_learning_evidence:{error}") from error
        examples = _rescale_examples(evidence.examples, rows, config_map, values)
        source_boundary = max((item.event_uid for item in examples), default=None)
        holdout: tuple[LearningExample, ...] = ()
        if examples:
            try:
                _, holdout, _ = split_gates(
                    examples,
                    holdout_fraction=self._holdout_fraction,
                )
            except ValueError:
                holdout = ()
        holdout_ids = {item.injection_id for item in holdout}
        holdout_boundaries = tuple(
            boundary
            for boundary in evidence.share_boundaries
            if boundary.injection_id in holdout_ids
        )
        base_runtime = _runtime(base)
        incumbent_values = _values(base)
        incumbent_score = (
            challenger_score(
                _rescale_examples(holdout, rows, config_map, incumbent_values),
                weights=_weight_tuple(incumbent_values),
                bias_offsets=base_runtime.bias_offsets,
                project_offsets=base_runtime.project_offsets,
                axes=base_runtime.axes,
                thread_weight=base_runtime.params.thread_weight,
                where_weight=base_runtime.params.where_weight,
                tau=incumbent_values.tau,
                share_boundaries=holdout_boundaries,
                memory_context_share=incumbent_values.memory_context_share,
            )
            if holdout
            else None
        )
        preview_score = (
            challenger_score(
                holdout,
                weights=_weight_tuple(values),
                bias_offsets=base_runtime.bias_offsets,
                project_offsets=base_runtime.project_offsets,
                axes=base_runtime.axes,
                thread_weight=base_runtime.params.thread_weight,
                where_weight=base_runtime.params.where_weight,
                tau=values.tau,
                share_boundaries=holdout_boundaries,
                memory_context_share=values.memory_context_share,
            )
            if holdout
            else None
        )
        incumbent_accuracy = _accuracy(incumbent_score, len(holdout))
        accuracy = _accuracy(preview_score, len(holdout))
        delta = (
            None
            if accuracy is None or incumbent_accuracy is None
            else accuracy - incumbent_accuracy
        )
        digest_payload = {
            "base_version": base.version,
            "values": values.model_dump(mode="json"),
            "source_boundary": source_boundary,
            "holdout_dispositions": len(holdout),
            "incumbent_accuracy_percent": _optional_decimal(incumbent_accuracy),
            "accuracy_percent": _optional_decimal(accuracy),
            "delta_percent": _optional_decimal(delta),
        }
        digest = hashlib.sha256(
            json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        terrain = []
        if include_terrain and holdout:
            for tau_step in range(9):
                for share_step in range(9):
                    tau, share = tau_step / 8, 0.01 + share_step * 0.49 / 8
                    result = challenger_score(
                        holdout,
                        weights=_weight_tuple(values),
                        bias_offsets=base_runtime.bias_offsets,
                        project_offsets=base_runtime.project_offsets,
                        axes=base_runtime.axes,
                        thread_weight=base_runtime.params.thread_weight,
                        where_weight=base_runtime.params.where_weight,
                        tau=tau,
                        share_boundaries=holdout_boundaries,
                        memory_context_share=share,
                    )
                    terrain.append(
                        {
                            "tau": tau,
                            "share": share,
                            "agreement": 100
                            * (1 - result.disagreements / (len(holdout) + len(holdout_boundaries))),
                        }
                    )
        return ScorerSimulationResponse(
            terrain=terrain,
            simulation_digest=digest,
            base_version=base.version,
            values=values,
            source_boundary=source_boundary,
            holdout_dispositions=len(holdout),
            accuracy_percent=_optional_decimal(accuracy),
            incumbent_accuracy_percent=_optional_decimal(incumbent_accuracy),
            delta_percent=_optional_decimal(delta),
            instant=InstantSimulation(status="not_requested", injection_id=None, candidates=[]),
            slice=AccuracySlice(parameter_id="scorer.tau", points=[]),
        )

    async def _slice(
        self,
        session: AsyncSession,
        base: ScorerConfigRow,
        values: ScorerValues,
        parameter_id: str,
    ) -> AccuracySlice:
        points: list[AccuracySlicePoint] = []
        for value in _slice_values(values, parameter_id):
            candidate = _with_parameter(values, parameter_id, value)
            receipt = await self._deep_receipt(session, base, candidate)
            points.append(
                AccuracySlicePoint(value=value, accuracy_percent=receipt.accuracy_percent)
            )
        return AccuracySlice(parameter_id=parameter_id, points=points)  # type: ignore[arg-type]

    async def activate_proposal(
        self,
        version: str,
        body: ActivateScorerConfigRequest,
    ) -> ScorerConfigurationView:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"),
                    {"key": _CONTROL_ADVISORY_LOCK_KEY},
                )
                replay = await session.get(ScorerActivationRow, body.event_uid)
                if replay is not None:
                    if (
                        replay.version != version
                        or replay.actor_class != body.actor_class
                        or replay.machine_id != body.machine_id
                        or replay.reason != "learner_proposal"
                    ):
                        raise M2KStateError("event_uid_conflict")
                    target = await session.get(ScorerConfigRow, version)
                    if target is None:
                        raise M2KStateError("missing_version")
                    return _config_view(target)
                active = await _active_config(session)
                target = await session.get(ScorerConfigRow, version)
                if target is None:
                    raise M2KStateError("missing_version")
                learner = target.params.get("_learner")
                if (
                    target.active
                    or not isinstance(learner, Mapping)
                    or learner.get("status") != "proposed"
                ):
                    raise M2KStateError("not_proposed")
                changes = _changes(_values(active), _values(target))
                active.active = False
                await session.flush()
                target.active = True
                session.add(
                    ScorerActivationRow(
                        event_uid=body.event_uid,
                        version=target.version,
                        previous_version=active.version,
                        actor_class=body.actor_class,
                        machine_id=body.machine_id,
                        reason="learner_proposal",
                        changes=changes,
                    )
                )
                await session.flush()
                optimization_runs = (
                    (
                        await session.execute(
                            select(OptimizationRun).where(
                                OptimizationRun.challenger_version == target.version
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for optimization_run in optimization_runs:
                    if await session.get(OptimizationRunAdoption, optimization_run.run_uid) is None:
                        session.add(
                            OptimizationRunAdoption(
                                run_uid=optimization_run.run_uid,
                                tap_event_uid=body.event_uid,
                            )
                        )
                return _config_view(target)


async def _transaction_time(session: AsyncSession) -> datetime:
    value = await session.scalar(select(func.transaction_timestamp()))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RuntimeError("Postgres returned an invalid snapshot timestamp")
    return value


async def _revision_map(
    session: AsyncSession, memory_ids: list[UUID]
) -> dict[UUID, list[RevisionTrailItem]]:
    if not memory_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(MemoryRevisionRow)
                .where(MemoryRevisionRow.memory_id.in_(memory_ids))
                .order_by(
                    MemoryRevisionRow.memory_id,
                    MemoryRevisionRow.ts,
                    MemoryRevisionRow.rev_uid,
                )
            )
        )
        .scalars()
        .all()
    )
    result: dict[UUID, list[RevisionTrailItem]] = defaultdict(list)
    for row in rows:
        result[row.memory_id].append(
            RevisionTrailItem(
                rev_uid=row.rev_uid,
                parent_uid=row.parent_uid,
                revision=row.revision,
                ts=row.ts,
                reason=row.reason,
                label=row.label,
                body=row.body,
            )
        )
    return dict(result)


def _memory_unit(row: MemoryUnitRow) -> MemoryUnit:
    return MemoryUnit(
        memory_id=row.id,
        principal_id=row.principal_id,
        label=row.label,
        body=row.body,
        kind=row.kind,  # type: ignore[arg-type]
        keywords=list(row.keywords),
        project_key=row.project_key,
        thread_origin=row.thread_origin,
        origin_thread_id=row.origin_thread_id,
        origin_path=row.origin_path,
        origin_location=row.origin_location,
        pin=row.pin,
        status=row.status,  # type: ignore[arg-type]
        revision=row.revision,
        stats=dict(row.stats),
        bias=row.bias,
        embedding_model=row.embedding_model,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _values(row: ScorerConfigRow) -> ScorerValues:
    try:
        raw_share = row.params.get("memory_context_share", row.params.get("budget_pct"))
        return ScorerValues(
            tau=float(row.params["tau"]),
            top_k=int(row.params["top_k"]),
            memory_context_share=float(raw_share),
            half_life_time_days=float(row.params["half_life_time_days"]),
            half_life_hist_days=float(row.params["half_life_hist_days"]),
            weights={name: float(row.weights[name]) for name in FEATURE_NAMES},
        )
    except (KeyError, TypeError, ValueError) as error:
        raise M2KStateError(f"invalid_scorer:{row.version}") from error


def _config_view(row: ScorerConfigRow) -> ScorerConfigurationView:
    learner = row.params.get("_learner")
    proposed = (
        isinstance(learner, Mapping)
        and learner.get("status") == "proposed"
        and "_control" not in row.params
    )
    status = "active" if row.active else "proposed" if proposed else "inactive"
    replay = learner.get("replay") if isinstance(learner, Mapping) else None
    return ScorerConfigurationView(
        version=row.version,
        created_at=row.created_at,
        status=status,
        values=_values(row),
        replay=dict(replay) if isinstance(replay, Mapping) else None,
        axes=row.params.get("axes", {}),
        project_offsets=row.params.get("project_offsets", {}),
    )


def _activation_view(row: ScorerActivationRow) -> ScorerActivationView:
    return ScorerActivationView(
        event_uid=row.event_uid,
        version=row.version,
        previous_version=row.previous_version,
        actor_class=row.actor_class,  # type: ignore[arg-type]
        machine_id=row.machine_id,
        reason=row.reason,  # type: ignore[arg-type]
        changes=dict(row.changes),
        ts=row.ts,
    )


def _accuracy_point(row: ScorerConfigRow) -> AccuracyPoint:
    learner = row.params.get("_learner")
    replay = learner.get("replay") if isinstance(learner, Mapping) else None
    challenger = replay.get("challenger") if isinstance(replay, Mapping) else None
    holdout = learner.get("holdout_dispositions") if isinstance(learner, Mapping) else None
    holdout_weight_raw = learner.get("holdout_weight") if isinstance(learner, Mapping) else None
    disagreements = challenger.get("disagreements") if isinstance(challenger, Mapping) else None
    weighted_wrong_raw = (
        challenger.get("weighted_disagreements") if isinstance(challenger, Mapping) else None
    )
    try:
        holdout_weight = (
            Decimal(holdout_weight_raw) if isinstance(holdout_weight_raw, str) else None
        )
        weighted_wrong = (
            Decimal(weighted_wrong_raw) if isinstance(weighted_wrong_raw, str) else None
        )
        if isinstance(challenger, Mapping):
            if isinstance(disagreements, int):
                disagreements -= int(challenger.get("share_disagreements", 0))
            if weighted_wrong is not None:
                weighted_wrong -= Decimal(challenger.get("weighted_share_disagreements", "0"))
    except Exception:
        holdout_weight = None
        weighted_wrong = None
    if (
        isinstance(holdout, int)
        and not isinstance(holdout, bool)
        and holdout > 0
        and isinstance(disagreements, int)
        and not isinstance(disagreements, bool)
        and 0 <= disagreements <= holdout
        and holdout_weight is not None
        and holdout_weight.is_finite()
        and holdout_weight > 0
        and weighted_wrong is not None
        and weighted_wrong.is_finite()
        and 0 <= weighted_wrong <= holdout_weight
        and "_control" not in row.params
    ):
        accuracy = Decimal(100) * (holdout_weight - weighted_wrong) / holdout_weight
        return AccuracyPoint(
            version=row.version,
            created_at=row.created_at,
            status="measured",
            accuracy_percent=_decimal_string(accuracy),
            holdout_dispositions=holdout,
            disagreements=disagreements,
            weighted_dispositions=_decimal_string(holdout_weight),
            weighted_wrong=_decimal_string(weighted_wrong),
        )
    return AccuracyPoint(
        version=row.version,
        created_at=row.created_at,
        status="not_recorded",
        accuracy_percent=None,
        holdout_dispositions=None,
        disagreements=None,
        weighted_dispositions=None,
        weighted_wrong=None,
    )


def _learning_view(
    *,
    evidence_examples: tuple[LearningExample, ...],
    hygiene_excluded: int,
    events: list[InjectionEvent],
    active_version: str,
    runs: list[LearnerRun],
    activations: list[ScorerActivationRow],
    minimum_dispositions: int,
    share_tuning_minimum: int,
    retrain_signal_stride: int,
) -> LearningView:
    eligible = len(evidence_examples)
    qualifying_runs = [run for run in runs if run.eligible_dispositions >= minimum_dispositions]
    cursor = (
        max(
            qualifying_runs,
            key=lambda row: (row.eligible_dispositions, row.ts, row.run_uid),
        ).eligible_dispositions
        if qualifying_runs
        else None
    )
    signals_since = max(0, eligible - (cursor or 0))
    remaining_to_floor = max(0, minimum_dispositions - eligible)
    floor_met = eligible >= minimum_dispositions
    share_tuning_remaining = max(0, share_tuning_minimum - eligible)
    signals_until_next = (
        remaining_to_floor
        if not floor_met
        else 0
        if cursor is None
        else max(0, retrain_signal_stride - signals_since)
    )
    by_event_uid = {row.event_uid: row for row in events}
    active_examples = [
        (example, by_event_uid[example.event_uid])
        for example in evidence_examples
        if by_event_uid[example.event_uid].scorer_version == active_version
    ]
    right, wrong, weighted_right, weighted_wrong = _agreement_totals(active_examples)
    weighted_total = weighted_right + weighted_wrong
    agreement_percent = (
        None
        if weighted_total == 0
        else _decimal_string(Decimal(100) * weighted_right / weighted_total)
    )
    live_points: list[LiveAgreementPoint] = []
    for index, (_, row) in enumerate(active_examples):
        window = active_examples[max(0, index + 1 - retrain_signal_stride) : index + 1]
        point_right, point_wrong, point_weighted_right, point_weighted_wrong = _agreement_totals(
            window
        )
        point_total = point_weighted_right + point_weighted_wrong
        live_points.append(
            LiveAgreementPoint(
                event_uid=row.event_uid,
                ts=row.ts,
                scorer_version=row.scorer_version,
                right=point_right,
                wrong=point_wrong,
                weighted_right=_decimal_string(point_weighted_right),
                weighted_wrong=_decimal_string(point_weighted_wrong),
                weighted_agreement_percent=_decimal_string(
                    Decimal(100) * point_weighted_right / point_total
                ),
            )
        )
    run_views = [_learner_run_view(row) for row in runs]
    annotations = [
        LearningAnnotation(
            kind="force_values" if "_force" in row.changes else "activation",
            event_uid=row.event_uid,
            ts=row.ts,
            version=row.version,
            result=None,
        )
        for row in activations
    ]
    annotations.extend(
        LearningAnnotation(
            kind="retrain",
            event_uid=row.run_uid,
            ts=row.ts,
            version=row.proposal_version or row.incumbent_version,
            result=row.result,  # type: ignore[arg-type]
        )
        for row in runs
    )
    annotations.sort(key=lambda item: (item.ts, item.event_uid))
    return LearningView(
        eligible_dispositions=eligible,
        hygiene_excluded_dispositions=hygiene_excluded,
        minimum_dispositions=minimum_dispositions,
        remaining_to_floor=remaining_to_floor,
        floor_met=floor_met,
        share_tuning_minimum=share_tuning_minimum,
        share_tuning_remaining=share_tuning_remaining,
        share_tuning_active=eligible >= share_tuning_minimum,
        retrain_signal_stride=retrain_signal_stride,
        evaluated_through=cursor,
        signals_since_last_run=signals_since,
        signals_until_next_run=signals_until_next,
        active_scorer_version=active_version,
        right=right,
        wrong=wrong,
        weighted_right=_decimal_string(weighted_right),
        weighted_wrong=_decimal_string(weighted_wrong),
        weighted_agreement_percent=agreement_percent,
        live_agreement=live_points,
        retrain_runs=run_views,
        annotations=annotations,
    )


def _agreement_totals(
    examples: list[tuple[LearningExample, InjectionEvent]],
) -> tuple[int, int, Decimal, Decimal]:
    right = 0
    wrong = 0
    weighted_right = Decimal(0)
    weighted_wrong = Decimal(0)
    for example, _ in examples:
        if example.recorded_injected == example.target_injected:
            right += 1
            weighted_right += example.actor_weight
        else:
            wrong += 1
            weighted_wrong += example.actor_weight
    return right, wrong, weighted_right, weighted_wrong


def _learner_run_view(row: LearnerRun) -> LearnerRunView:
    return LearnerRunView(
        run_uid=row.run_uid,
        trigger=row.trigger,  # type: ignore[arg-type]
        result=row.result,  # type: ignore[arg-type]
        incumbent_version=row.incumbent_version,
        proposal_version=row.proposal_version,
        eligible_dispositions=row.eligible_dispositions,
        training_dispositions=row.training_dispositions,
        holdout_dispositions=row.holdout_dispositions,
        training_pairs=row.training_pairs,
        source_boundary=row.source_boundary,
        incumbent=row.incumbent,  # type: ignore[arg-type]
        challenger=row.challenger,  # type: ignore[arg-type]
        reason=row.reason,
        ts=row.ts,
    )


def _candidate_histories(
    events: list[InjectionEvent],
    configs: Mapping[str, ScorerConfigRow],
) -> list[CandidateScoreHistory]:
    grouped: dict[UUID, list[CandidateScorePoint]] = defaultdict(list)
    identity: dict[UUID, tuple[str, str]] = {}
    for event in events:
        config = configs.get(event.scorer_version)
        if config is None:
            raise M2KStateError(f"missing_event_scorer:{event.scorer_version}")
        frozen = event.features.get("_memory")
        label = frozen.get("label") if isinstance(frozen, Mapping) else ""
        identity[event.memory_id] = (
            label if isinstance(label, str) else "",
            event.memory_kind,
        )
        raw_location = event.features.get("loc")
        raw_thread = event.features.get("thread")
        raw_where = event.features.get("where")
        features = MemoryFeatures(
            **{name: float(event.features[name]) for name in FEATURE_NAMES},
            loc=(
                float(raw_location)
                if isinstance(raw_location, (int, float)) and not isinstance(raw_location, bool)
                else None
            ),
            thread=(
                float(raw_thread)
                if isinstance(raw_thread, (int, float)) and not isinstance(raw_thread, bool)
                else None
            ),
            where=(
                float(raw_where)
                if isinstance(raw_where, (int, float)) and not isinstance(raw_where, bool)
                else None
            ),
        )
        location_scale = (
            1.0 - float(config.params.get("location_weight", 0.0))
            if features.loc is not None
            else 1.0
        )
        runtime = _runtime(config)
        project_weights = runtime.weights_for_project(event.project_key)
        contributions = {
            name: Decimal(str(getattr(features, name)))
            * Decimal(str(getattr(project_weights, name)))
            * Decimal(str(location_scale))
            for name in FEATURE_NAMES
        }
        location_contribution = (
            None
            if features.loc is None
            else Decimal(str(features.loc))
            * Decimal(str(config.params.get("location_weight", 0.0)))
        )
        thread_contribution = None
        if features.thread is not None:
            thread_weight = Decimal(str(config.params.get("thread_weight", 0.0)))
            thread_scale = Decimal(1) - thread_weight
            contributions = {name: value * thread_scale for name, value in contributions.items()}
            if location_contribution is not None:
                location_contribution *= thread_scale
            thread_contribution = Decimal(str(features.thread)) * thread_weight
        where_contribution = None
        if features.where is not None:
            where_weight = Decimal(str(config.params.get("where_weight", 0.0)))
            where_scale = Decimal(1) - where_weight
            contributions = {name: value * where_scale for name, value in contributions.items()}
            if location_contribution is not None:
                location_contribution *= where_scale
            if thread_contribution is not None:
                thread_contribution *= where_scale
            where_contribution = Decimal(str(features.where)) * where_weight
        stored_score = Decimal(str(event.score))
        axis_contributions = {}
        for name, axis in sorted(runtime.axes.items()):
            value = axis.value(event.features)
            if value is None or not axis.weight:
                continue
            scale = Decimal(1) - Decimal(str(axis.weight))
            contributions = {key: amount * scale for key, amount in contributions.items()}
            axis_contributions = {key: amount * scale for key, amount in axis_contributions.items()}
            location_contribution = (
                None if location_contribution is None else location_contribution * scale
            )
            thread_contribution = (
                None if thread_contribution is None else thread_contribution * scale
            )
            where_contribution = None if where_contribution is None else where_contribution * scale
            axis_contributions[name] = Decimal(str(value)) * Decimal(str(axis.weight))
        bias = stored_score - sum(contributions.values(), start=Decimal(0))
        bias -= sum(axis_contributions.values(), start=Decimal(0))
        if location_contribution is not None:
            bias -= location_contribution
        if thread_contribution is not None:
            bias -= thread_contribution
        if where_contribution is not None:
            bias -= where_contribution
        grouped[event.memory_id].append(
            CandidateScorePoint(
                event_uid=event.event_uid,
                injection_id=event.injection_id,
                ts=event.ts,
                scorer_version=event.scorer_version,
                score=_decimal_string(stored_score),
                rank=event.rank,
                shown_as=event.shown_as,  # type: ignore[arg-type]
                outcome=event.outcome,
                features=features,
                axis_contributions={
                    name: _decimal_string(value) for name, value in axis_contributions.items()
                },
                contributions=ContributionBreakdown(
                    **{
                        **{name: _decimal_string(value) for name, value in contributions.items()},
                        "loc": (
                            None
                            if location_contribution is None
                            else _decimal_string(location_contribution)
                        ),
                        "thread": (
                            None
                            if thread_contribution is None
                            else _decimal_string(thread_contribution)
                        ),
                        "where": None
                        if where_contribution is None
                        else _decimal_string(where_contribution),
                        "bias": _decimal_string(bias),
                    }
                ),
            )
        )
    return [
        CandidateScoreHistory(
            memory_id=memory_id,
            label=identity[memory_id][0],
            kind=identity[memory_id][1],
            points=points,
        )
        for memory_id, points in sorted(grouped.items(), key=lambda item: item[0].int)
    ]


def _changes(old: ScorerValues, new: ScorerValues) -> dict[str, Any]:
    old_flat = _flat_values(old)
    new_flat = _flat_values(new)
    return {
        key: {"old": old_flat[key], "new": new_flat[key]}
        for key in old_flat
        if old_flat[key] != new_flat[key]
    }


def _flat_values(values: ScorerValues) -> dict[str, float | int]:
    result: dict[str, float | int] = {
        "scorer.tau": values.tau,
        "scorer.top_k": values.top_k,
        "scorer.memory_context_share": values.memory_context_share,
        "scorer.half_life_time_days": values.half_life_time_days,
        "scorer.half_life_hist_days": values.half_life_hist_days,
    }
    result.update({f"scorer.weight.{name}": value for name, value in values.weights.items()})
    return result


def _validate_runtime_config(
    version: str,
    weights: Mapping[str, float],
    params: Mapping[str, Any],
) -> None:
    try:
        RuntimeScorerConfig.from_mappings(version=version, weights=weights, params=params)
    except (KeyError, TypeError, ValueError) as error:
        raise M2KStateError("invalid_values") from error


def _runtime(row: ScorerConfigRow) -> RuntimeScorerConfig:
    try:
        return RuntimeScorerConfig.from_mappings(
            version=row.version,
            weights=row.weights,
            params=row.params,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise M2KStateError(f"invalid_scorer:{row.version}") from error


def _weight_tuple(values: ScorerValues) -> tuple[float, float, float, float, float, float]:
    return tuple(values.weights[name] for name in FEATURE_NAMES)  # type: ignore[return-value]


def _example(
    row: InjectionEvent,
    source: RuntimeScorerConfig,
    values: ScorerValues,
    labeled: tuple[bool, Decimal],
) -> LearningExample:
    original = tuple(_feature(row, name) for name in FEATURE_NAMES)
    adjusted = list(original)
    adjusted[2] = _rescale_decay(
        original[2], source.params.half_life_time_days, values.half_life_time_days
    )
    adjusted[5] = _rescale_decay(
        original[5], source.params.half_life_hist_days, values.half_life_hist_days
    )
    raw_location = row.features.get("loc")
    location = (
        float(raw_location)
        if isinstance(raw_location, (int, float)) and not isinstance(raw_location, bool)
        else None
    )
    raw_thread = row.features.get("thread")
    thread = (
        float(raw_thread)
        if isinstance(raw_thread, (int, float)) and not isinstance(raw_thread, bool)
        else None
    )
    raw_where = row.features.get("where")
    where = (
        float(raw_where)
        if isinstance(raw_where, (int, float)) and not isinstance(raw_where, bool)
        else None
    )
    source_score = score_features(
        row.features,
        weights=source.weights_for_project(row.project_key),
        params=source.params,
        axes=source.axes,
    )
    baseline_bias = float(row.score) - source_score
    baseline_bias -= source.bias_offset(row.memory_id)
    frozen = row.features.get("_memory")
    body = frozen.get("body") if isinstance(frozen, Mapping) else None
    if not isinstance(body, str):
        raise M2KStateError(f"event_not_replayable:{row.event_uid}")
    target, actor_weight = labeled
    return LearningExample(
        event_uid=row.event_uid,
        injection_id=row.injection_id,
        memory_id=row.memory_id,
        ts=row.ts,
        features=tuple(adjusted),  # type: ignore[arg-type]
        baseline_bias=baseline_bias,
        target_injected=target,
        actor_weight=actor_weight,
        shown_as=row.shown_as,  # type: ignore[arg-type]
        body_tokens=cl100k_token_count(body),
        location_feature=location,
        location_weight=source.params.location_weight,
        thread_feature=thread,
        where_feature=where,
        where_weight=source.params.where_weight,
        project_key=row.project_key,
    )


def _rescale_examples(
    examples: tuple[LearningExample, ...],
    rows: list[InjectionEvent],
    configs: Mapping[str, RuntimeScorerConfig],
    values: ScorerValues,
) -> tuple[LearningExample, ...]:
    by_uid = {row.event_uid: row for row in rows}
    result: list[LearningExample] = []
    for existing in examples:
        row = by_uid[existing.event_uid]
        source = configs[row.scorer_version]
        result.append(
            _example(
                row,
                source,
                values,
                (existing.target_injected, existing.actor_weight),
            )
        )
    return tuple(result)


def _feature(row: InjectionEvent, name: str) -> float:
    value = row.features.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise M2KStateError(f"invalid_event_feature:{row.event_uid}:{name}")
    normalized = float(value)
    if not math.isfinite(normalized) or not 0 <= normalized <= 1:
        raise M2KStateError(f"invalid_event_feature:{row.event_uid}:{name}")
    return normalized


def _rescale_decay(value: float, source_half_life: float, target_half_life: float) -> float:
    return 0.0 if value == 0 else value ** (source_half_life / target_half_life)


def _accuracy(score: Any, count: int) -> Decimal | None:
    if score is None or count == 0:
        return None
    # Share-boundary errors are separate decisions, not extra wrong memories.
    memory_disagreements = score.disagreements - score.share_disagreements
    return Decimal(100) * Decimal(count - memory_disagreements) / Decimal(count)


def _optional_decimal(value: Decimal | None) -> str | None:
    return None if value is None else _decimal_string(value)


async def _instant(
    session: AsyncSession,
    *,
    principal_id: str,
    injection_id: UUID | None,
    preview: ScorerConfigRow,
    preview_values: ScorerValues,
) -> InstantSimulation:
    if injection_id is None:
        return InstantSimulation(status="not_requested", injection_id=None, candidates=[])
    rows = (
        (
            await session.execute(
                select(InjectionEvent)
                .where(
                    InjectionEvent.injection_id == injection_id,
                    InjectionEvent.principal_id == principal_id,
                )
                .order_by(InjectionEvent.rank, InjectionEvent.event_uid)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise M2KStateError("missing_injection")
    prepare_meta = rows[0].features.get("_prepare")
    context_tokens = (
        prepare_meta.get("model_context_tokens") if isinstance(prepare_meta, Mapping) else None
    )
    if (
        isinstance(context_tokens, bool)
        or not isinstance(context_tokens, int)
        or context_tokens <= 0
    ):
        return InstantSimulation(status="not_replayable", injection_id=injection_id, candidates=[])
    config_rows = (await session.execute(select(ScorerConfigRow))).scalars().all()
    configs = {row.version: _runtime(row) for row in config_rows}
    preview_params = {**preview.params, **preview_values.model_dump(exclude={"weights"})}
    preview_runtime = RuntimeScorerConfig.from_mappings(
        version=preview.version, weights=preview_values.weights, params=preview_params
    )
    candidates: list[dict[str, Any]] = []
    for row in rows:
        source = configs.get(row.scorer_version)
        if source is None:
            raise M2KStateError(f"missing_event_scorer:{row.scorer_version}")
        original = tuple(_feature(row, name) for name in FEATURE_NAMES)
        adjusted = list(original)
        adjusted[2] = _rescale_decay(
            original[2], source.params.half_life_time_days, preview_values.half_life_time_days
        )
        adjusted[5] = _rescale_decay(
            original[5], source.params.half_life_hist_days, preview_values.half_life_hist_days
        )
        baseline_bias = float(row.score) - score_features(
            row.features,
            weights=source.weights_for_project(row.project_key),
            params=source.params,
            axes=source.axes,
        )
        baseline_bias -= source.bias_offset(row.memory_id)
        preview_score = (
            score_features(
                {**row.features, **dict(zip(FEATURE_NAMES, adjusted, strict=True))},
                weights=preview_runtime.weights_for_project(row.project_key),
                params=preview_runtime.params,
                axes=preview_runtime.axes,
            )
            + baseline_bias
            + preview_runtime.bias_offset(row.memory_id)
        )
        frozen = row.features.get("_memory")
        if not isinstance(frozen, Mapping) or not isinstance(frozen.get("body"), str):
            return InstantSimulation(
                status="not_replayable", injection_id=injection_id, candidates=[]
            )
        candidates.append(
            {
                "row": row,
                "label": frozen.get("label") if isinstance(frozen.get("label"), str) else "",
                "pin": bool(frozen.get("pin")) or row.shown_as == "pinned",
                "token_cost": cl100k_token_count(frozen["body"]),
                "score": preview_score,
            }
        )
    pins = sorted(
        (item for item in candidates if item["pin"]), key=lambda item: item["row"].memory_id.int
    )
    regular = sorted(
        (item for item in candidates if not item["pin"]),
        key=lambda item: (-item["score"], item["row"].memory_id.int),
    )
    ordered = [*pins, *regular]
    preview_selected = {item["row"].memory_id for item in pins}
    remaining = max(0, int(preview_values.memory_context_share * context_tokens))
    for item in regular:
        if item["score"] >= preview_values.tau and item["token_cost"] <= remaining:
            preview_selected.add(item["row"].memory_id)
            remaining -= item["token_cost"]
    comparisons: list[ScorerComparisonRow] = []
    for rank, item in enumerate(ordered, start=1):
        row = item["row"]
        incumbent_selected = _event_selected(row)
        selected = row.memory_id in preview_selected
        disposition_name = {
            (True, True): "also_shown",
            (False, True): "would_add",
            (True, False): "would_drop",
            (False, False): "still_out",
        }[(incumbent_selected, selected)]
        comparisons.append(
            ScorerComparisonRow(
                memory_id=row.memory_id,
                label=item["label"],
                incumbent_score=_decimal_string(row.score),
                preview_score=_decimal_string(item["score"]),
                score_delta=_decimal_string(Decimal(str(item["score"])) - Decimal(str(row.score))),
                incumbent_rank=row.rank,
                preview_rank=rank,
                incumbent_selected=incumbent_selected,
                preview_selected=selected,
                disposition=disposition_name,  # type: ignore[arg-type]
            )
        )
    return InstantSimulation(status="ready", injection_id=injection_id, candidates=comparisons)


def _event_selected(row: InjectionEvent) -> bool:
    if row.outcome is None:
        return row.shown_as not in {"near_miss", "budget_cut"}
    return row.outcome in {"kept", "added_back", "cited", "auto_entered", "mid_thread_added"}


def _slice_values(values: ScorerValues, parameter_id: str) -> tuple[float | int, ...]:
    if parameter_id == "scorer.top_k":
        return tuple(range(1, 9))
    if parameter_id == "scorer.memory_context_share":
        return tuple(
            MIN_MEMORY_CONTEXT_SHARE
            + index * (MAX_MEMORY_CONTEXT_SHARE - MIN_MEMORY_CONTEXT_SHARE) / 8
            for index in range(9)
        )
    if parameter_id == "scorer.tau" or parameter_id.startswith("scorer.weight."):
        return tuple(index / 8 for index in range(9))
    current = _flat_values(values)[parameter_id]
    raw = [float(current) * (0.25 + index * (1.75 / 8)) for index in range(9)]
    return tuple(raw)


def _with_parameter(values: ScorerValues, parameter_id: str, value: float | int) -> ScorerValues:
    payload = values.model_dump(mode="python")
    if parameter_id.startswith("scorer.weight."):
        feature = parameter_id.rsplit(".", 1)[1]
        weights = dict(payload["weights"])
        target = float(value)
        remainder = 1.0 - target
        other_names = [name for name in FEATURE_NAMES if name != feature]
        other_total = math.fsum(weights[name] for name in other_names)
        if other_total == 0:
            for name in other_names:
                weights[name] = remainder / len(other_names)
        else:
            for name in other_names:
                weights[name] = remainder * weights[name] / other_total
        weights[feature] = target
        weights[other_names[-1]] += 1.0 - math.fsum(weights.values())
        payload["weights"] = weights
    else:
        key = parameter_id.removeprefix("scorer.")
        payload[key] = int(value) if key == "top_k" else float(value)
    return ScorerValues(**payload)


async def _active_config(session: AsyncSession) -> ScorerConfigRow:
    rows = (
        (await session.execute(select(ScorerConfigRow).where(ScorerConfigRow.active.is_(True))))
        .scalars()
        .all()
    )
    if len(rows) != 1:
        raise M2KStateError("invalid_active_scorer")
    return rows[0]


async def _validate_control_replay(
    session: AsyncSession,
    activation: ScorerActivationRow,
    target_version: str,
    body: CreateScorerConfigRequest,
) -> ScorerConfigurationView:
    target = await session.get(ScorerConfigRow, target_version)
    force = activation.changes.get("_force")
    if (
        target is None
        or activation.version != target_version
        or activation.previous_version != body.base_version
        or activation.actor_class != body.actor_class
        or activation.machine_id != body.machine_id
        or activation.reason != "human_control"
        or _values(target) != body.values
        or not isinstance(force, Mapping)
        or force.get("simulation_digest") != body.simulation_digest
    ):
        raise M2KStateError("event_uid_conflict")
    return _config_view(target)


def _decimal_string(value: Any) -> str:
    if isinstance(value, Decimal):
        decimal = value
    elif isinstance(value, bool) or not isinstance(value, (int, float)):
        raise M2KStateError("invalid_decimal")
    else:
        if isinstance(value, float) and not math.isfinite(value):
            raise M2KStateError("invalid_decimal")
        decimal = Decimal(str(value))
    rendered = format(decimal, "f")
    if rendered.startswith("-0") and decimal == 0:
        return "0"
    return rendered


__all__ = ["M2KService", "M2KStateError", "SCORER_DESCRIPTORS"]
