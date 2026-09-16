"""Live-Postgres contract tests for M2F proposal persistence and hygiene."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import pytest
from conftest import ACTIVE_SCORER_VERSION, TOKEN, ScriptedEmbeddingProvider, basis_vector
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from spine.db.models import (
    InjectionEvent,
    InjectionEventAnnotation,
    LearnerRun,
    OptimizationRun,
    OptimizationRunAdoption,
)
from spine.db.models import ScorerConfig as ScorerConfigRow
from spine.learner.contracts import RetrainResponse
from spine.learner.locking import LEARNER_ADVISORY_LOCK_KEY
from spine.learner.service import LearnerService, LearnerSettings, OptimizationTrigger
from spine.learner.worker import LearnerWorker


async def test_compaction_route_authenticates_validates_and_only_wakes_worker(app):
    """SPEC D.2 144: a main-thread event queues optimization without blocking chat."""
    from spine.ids import mint_ulid

    calls = []

    class Worker:
        def notify(self, trigger):
            calls.append(trigger)

    app.state.learner_worker = Worker()
    body = {"event_uid": mint_ulid(), "thread_id": str(UUID(int=7))}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/v1/compactions", json=body)).status_code == 401
        headers = {"Authorization": f"Bearer {TOKEN}"}
        assert (
            await client.post("/v1/compactions", json={**body, "event_uid": "bad"}, headers=headers)
        ).status_code == 422
        assert not calls
        response = await client.post("/v1/compactions", json=body, headers=headers)
    assert response.status_code == 202
    assert calls == [OptimizationTrigger(event_uid=body["event_uid"], thread_id=UUID(int=7))]


def _settings(*, min_dispositions: int, win_margin: float) -> LearnerSettings:
    return LearnerSettings(
        min_dispositions=min_dispositions,
        holdout_fraction=0.5,
        passive_discount=0.25,
        pair_margin=0.8,
        bias_l2=1.0,
        win_margin=win_margin,
    )


def _service(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    min_dispositions: int = 4,
    retrain_signal_stride: int = 25,
    win_margin: float = 1.0,
    corpus_max_dispositions: int = 1000,
) -> LearnerService:
    return LearnerService(
        session_factory,
        settings=_settings(min_dispositions=min_dispositions, win_margin=win_margin),
        retrain_signal_stride=retrain_signal_stride,
        corpus_max_dispositions=corpus_max_dispositions,
    )


async def _reset_proposals(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(
            text("TRUNCATE optimization_run_adoption, optimization_run, learner_run")
        )
        await session.execute(
            delete(ScorerConfigRow).where(
                ScorerConfigRow.version.not_in(
                    ("v0", "m3f-location-v1", "m3ti-thread-v1", ACTIVE_SCORER_VERSION)
                )
            )
        )


@asynccontextmanager
async def _held_learner_lock(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    bind = session_factory.kw.get("bind")
    assert isinstance(bind, AsyncEngine)
    connection = await bind.connect()
    try:
        await connection.execute(
            text("SELECT pg_advisory_lock(:key)"),
            {"key": LEARNER_ADVISORY_LOCK_KEY},
        )
        await connection.commit()
        yield
    finally:
        if connection.in_transaction():
            await connection.rollback()
        released = await connection.scalar(
            text("SELECT pg_advisory_unlock(:key)"),
            {"key": LEARNER_ADVISORY_LOCK_KEY},
        )
        await connection.commit()
        await connection.close()
        assert released is True


async def _wait_for_learner_waiters(
    session_factory: async_sessionmaker[AsyncSession],
    expected: int,
) -> None:
    for _ in range(200):
        async with session_factory() as session:
            count = await session.scalar(
                text(
                    "SELECT count(*) FROM pg_locks "
                    "WHERE locktype = 'advisory' AND classid = 0 "
                    "AND objid = :key AND NOT granted"
                ),
                {"key": LEARNER_ADVISORY_LOCK_KEY},
            )
        if count is not None and count >= expected:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"expected {expected} learner advisory-lock waiters")


async def _learner_lock_count(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with session_factory() as session:
        count = await session.scalar(
            text(
                "SELECT count(*) FROM pg_locks "
                "WHERE locktype = 'advisory' AND classid = 0 AND objid = :key AND granted"
            ),
            {"key": LEARNER_ADVISORY_LOCK_KEY},
        )
    assert isinstance(count, int)
    return count


async def _insert_gate(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    gate: int,
    machine_id: str = "studio-mac",
    scorer_version: str = ACTIVE_SCORER_VERSION,
    thread_id: UUID | None = None,
) -> None:
    base = datetime(2026, 8, 3, tzinfo=UTC) + timedelta(hours=gate)
    injection_id = UUID(int=gate)
    rows = (
        InjectionEvent(
            event_uid=f"gate-{gate}-positive",
            injection_id=injection_id,
            thread_id=thread_id or UUID(int=100 + gate),
            agent_id="general",
            machine_id=machine_id,
            principal_id="owner",
            project_key=None,
            agent_kind="general",
            prompt_text="learner fixture",
            scorer_version=scorer_version,
            memory_id=UUID(int=1),
            memory_kind="fact",
            features={
                "sem": 1.0,
                "kw": 0.0,
                "time": 0.0,
                "proj": 0.0,
                "freq": 0.0,
                "hist": 0.0,
                "_memory": {"body": "positive evidence"},
            },
            score=0.42,
            rank=2,
            shown_as="near_miss",
            actor_class="human",
            outcome="added_back",
            ts=base,
        ),
        InjectionEvent(
            event_uid=f"gate-{gate}-negative",
            injection_id=injection_id,
            thread_id=thread_id or UUID(int=100 + gate),
            agent_id="general",
            machine_id=machine_id,
            principal_id="owner",
            project_key=None,
            agent_kind="general",
            prompt_text="learner fixture",
            scorer_version=scorer_version,
            memory_id=UUID(int=2),
            memory_kind="fact",
            features={
                "sem": 0.0,
                "kw": 0.0,
                "time": 0.0,
                "proj": 0.0,
                "freq": 0.0,
                "hist": 0.0,
                "_memory": {"body": "negative evidence"},
            },
            score=0.0,
            rank=1,
            shown_as="injected",
            actor_class="human",
            outcome="removed:not_relevant",
            ts=base,
        ),
    )
    async with session_factory() as session, session.begin():
        session.add_all(rows)


async def _insert_passive_disposition(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    gate: int,
) -> None:
    """Append one authentic autonomous keep without inventing a human signal."""

    async with session_factory() as session, session.begin():
        session.add(
            InjectionEvent(
                event_uid=f"gate-{gate}-passive",
                injection_id=UUID(int=gate),
                thread_id=UUID(int=100 + gate),
                agent_id="general",
                machine_id="studio-mac",
                principal_id="owner",
                project_key=None,
                agent_kind="general",
                prompt_text="learner fixture",
                scorer_version=ACTIVE_SCORER_VERSION,
                memory_id=UUID(int=3),
                memory_kind="fact",
                features={
                    "sem": 1.0,
                    "kw": 0.0,
                    "time": 0.0,
                    "proj": 0.0,
                    "freq": 0.0,
                    "hist": 0.0,
                    "_memory": {"body": "authentic autonomous evidence"},
                },
                score=0.42,
                rank=1,
                shown_as="injected",
                actor_class="passive",
                outcome="auto_entered",
                ts=datetime(2026, 8, 3, tzinfo=UTC) + timedelta(hours=gate),
            )
        )


@pytest.mark.asyncio
async def test_retrain_proposes_inactive_content_addressed_winner_idempotently(
    memory_app,
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-031 is defended by verifying that retrain proposes inactive content addressed winner
    idempotently; this prevents drift in the learner proposal authority.
    """
    await _reset_proposals(memory_session_factory)
    memory_app.state.learner_service = _service(memory_session_factory)
    await _insert_gate(memory_session_factory, gate=1)
    await _insert_gate(memory_session_factory, gate=2)

    first = await memory_client.post("/retrain")
    second = await memory_client.post("/retrain")

    assert first.status_code == 200
    assert second.status_code == 200
    payload = first.json()
    assert payload["status"] == "proposed"
    assert payload["eligible_dispositions"] == 4
    assert payload["training_dispositions"] == 2
    assert payload["holdout_dispositions"] == 2
    assert payload["training_pairs"] == 1
    assert payload["incumbent"]["weighted_disagreements"] == "2"
    assert payload["challenger"]["weighted_disagreements"] == "0"
    assert second.json()["proposal_version"] == payload["proposal_version"]
    async with memory_session_factory() as session:
        proposals = (
            (
                await session.execute(
                    select(ScorerConfigRow).where(
                        ScorerConfigRow.version.not_in(
                            (
                                "v0",
                                "m3f-location-v1",
                                "m3ti-thread-v1",
                                ACTIVE_SCORER_VERSION,
                            )
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.active is False
    assert proposal.version == payload["proposal_version"]
    assert sum(proposal.weights.values()) == pytest.approx(1.0)
    assert proposal.params["tau"] == 0.55
    assert proposal.params["_learner"]["status"] == "proposed"
    assert proposal.params["_learner"]["bias_offsets"]
    await _reset_proposals(memory_session_factory)


@pytest.mark.asyncio
async def test_real_retrain_records_complete_optimization_run_and_later_owner_tap(
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """P1.2.2 proves a real Postgres retrain is fully attributable without rewriting history."""

    await _reset_proposals(memory_session_factory)
    await _insert_gate(memory_session_factory, gate=81)
    await _insert_gate(memory_session_factory, gate=82)
    trigger_thread = UUID(int=8081)
    trigger = OptimizationTrigger(
        event_uid="01KZ0A00000000000000000001",
        thread_id=trigger_thread,
    )

    response = await _service(memory_session_factory).retrain(optimization_trigger=trigger)

    assert response.status == "proposed"
    assert response.proposal_version is not None
    async with memory_session_factory() as session:
        run = (await session.execute(select(OptimizationRun))).scalars().one()
        legacy = await session.get(LearnerRun, run.run_uid)
    assert legacy is not None
    assert run.loop == "injection_scoring"
    assert run.trigger_kind == "manual"
    assert run.trigger_event_uid == trigger.event_uid
    assert run.trigger_thread_id == trigger_thread
    assert len(run.corpus_fingerprint) == 64
    assert run.corpus_size == 4
    assert run.corpus_max_size == 1000
    assert run.corpus_stratification["strategy"] == "thread_round_robin_newest_oldest_v1"
    assert run.corpus_stratification["available"] == 4
    assert run.corpus_stratification["selected"] == 4
    assert len(run.corpus_stratification["threads"]) == 2
    assert run.incumbent_version == ACTIVE_SCORER_VERSION
    assert run.challenger_version == response.proposal_version
    assert run.incumbent_params["version"] == ACTIVE_SCORER_VERSION
    assert run.challenger_params is not None
    assert run.challenger_params["version"] == response.proposal_version
    assert set(run.backtest_scores) == {"train", "holdout"}
    assert set(run.backtest_scores["train"]) == {"incumbent", "challenger"}
    assert set(run.backtest_scores["holdout"]) == {"incumbent", "challenger"}
    assert run.verdict == "proposed"
    assert run.tie_break_applied["rule"] in {"replay_margin", "cheaper_at_exact_tie"}
    assert run.cost_refs == []
    assert run.started_at.tzinfo is not None
    assert run.completed_at >= run.started_at

    tap_event_uid = "01KZ0A00000000000000000002"
    activated = await memory_client.post(
        f"/v1/scorer-configs/{response.proposal_version}/activate",
        json={
            "event_uid": tap_event_uid,
            "actor_class": "human",
            "machine_id": "studio-mac",
        },
    )
    assert activated.status_code == 200
    async with memory_session_factory() as session:
        adoption = await session.get(OptimizationRunAdoption, run.run_uid)
    assert adoption is not None
    assert adoption.tap_event_uid == tap_event_uid
    assert adoption.adopted_at.tzinfo is not None


@pytest.mark.asyncio
async def test_optimization_corpus_is_capped_and_balanced_by_whole_thread_gates(
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """P1.2.2 prevents one long thread from consuming a bounded replay corpus."""

    await _reset_proposals(memory_session_factory)
    dominant_thread = UUID(int=9001)
    minority_thread = UUID(int=9002)
    for gate in (83, 84, 85):
        await _insert_gate(memory_session_factory, gate=gate, thread_id=dominant_thread)
    await _insert_gate(memory_session_factory, gate=86, thread_id=minority_thread)

    response = await _service(
        memory_session_factory,
        min_dispositions=4,
        corpus_max_dispositions=4,
    ).retrain()

    assert response.eligible_dispositions == 8
    assert response.training_dispositions + response.holdout_dispositions == 4
    async with memory_session_factory() as session:
        run = (await session.execute(select(OptimizationRun))).scalars().one()
    strata = {item["thread_id"]: item for item in run.corpus_stratification["threads"]}
    assert run.corpus_size == 4
    assert run.corpus_max_size == 4
    assert strata[str(dominant_thread)] == {
        "thread_id": str(dominant_thread),
        "available": 6,
        "selected": 2,
        "selected_gates": 1,
    }
    assert strata[str(minority_thread)] == {
        "thread_id": str(minority_thread),
        "available": 2,
        "selected": 2,
        "selected_gates": 1,
    }


@pytest.mark.asyncio
async def test_live_prepare_uses_incumbent_while_optimization_is_fitting(
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
    embedding_provider: ScriptedEmbeddingProvider,
) -> None:
    """P1.2.2 keeps the owner path live and on the incumbent during an optimization fit."""

    entered_fit = asyncio.Event()
    release_fit = asyncio.Event()

    class PausingLearnerService(LearnerService):
        async def _fit(self, session: AsyncSession, **kwargs: object):  # type: ignore[no-untyped-def]
            entered_fit.set()
            await release_fit.wait()
            return await super()._fit(session, **kwargs)  # type: ignore[arg-type]

    await _reset_proposals(memory_session_factory)
    await _insert_gate(memory_session_factory, gate=87)
    await _insert_gate(memory_session_factory, gate=88)
    service = PausingLearnerService(
        memory_session_factory,
        settings=_settings(min_dispositions=4, win_margin=1.0),
    )
    retrain_task = asyncio.create_task(service.retrain())
    await asyncio.wait_for(entered_fit.wait(), timeout=2)
    prompt = "live request while optimization fits"
    embedding_provider.set(prompt, basis_vector(0))

    prepared = await asyncio.wait_for(
        memory_client.post(
            "/v1/inject/prepare",
            json={
                "thread_id": str(UUID(int=9088)),
                "agent_id": "agent-1",
                "machine_id": "studio-mac",
                "principal_id": "owner",
                "project_key": "alpha",
                "prompt": prompt,
                "model_context_tokens": 8192,
            },
        ),
        timeout=2,
    )

    assert prepared.status_code == 200
    assert prepared.json()["scorer_version"] == ACTIVE_SCORER_VERSION
    release_fit.set()
    result = await asyncio.wait_for(retrain_task, timeout=2)
    assert result.status == "proposed"


@pytest.mark.asyncio
async def test_retrain_hygiene_excludes_whole_verification_gate(
    memory_app,
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-031 is defended by verifying that retrain hygiene excludes whole verification gate;
    this prevents drift in the learner proposal authority.
    """
    await _reset_proposals(memory_session_factory)
    memory_app.state.learner_service = _service(memory_session_factory, min_dispositions=1)
    await _insert_gate(memory_session_factory, gate=3, machine_id="m2f-sop-verification")

    response = await memory_client.post("/retrain")

    assert response.status_code == 200
    assert response.json() == {
        "status": "insufficient_data",
        "incumbent_version": ACTIVE_SCORER_VERSION,
        "proposal_version": None,
        "eligible_dispositions": 0,
        "training_dispositions": 0,
        "holdout_dispositions": 0,
        "training_pairs": 0,
        "incumbent": None,
        "challenger": None,
        "reason": "minimum disposition floor not reached: 0/1",
    }


@pytest.mark.asyncio
async def test_retrain_hygiene_excludes_whole_annotated_gate(
    memory_app,
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-053/F033 make one verification-only annotation exclude its whole evidence gate."""

    await _reset_proposals(memory_session_factory)
    memory_app.state.learner_service = _service(memory_session_factory, min_dispositions=1)
    await _insert_gate(memory_session_factory, gate=4)
    async with memory_session_factory() as session, session.begin():
        session.add(
            InjectionEventAnnotation(
                target_event_uid="gate-4-positive",
                kind="verification_only",
                target_principal_id="owner",
                target_machine_id="studio-mac",
                reason="F033 production-shaped overlay",
                annotator_principal_id="m2za-sop-verification",
                annotator_machine_id="m2za-sop-verification",
                annotator_origin_agent="verification:m2za",
            )
        )

    response = await memory_client.post("/retrain")

    assert response.status_code == 200
    assert response.json() == {
        "status": "insufficient_data",
        "incumbent_version": ACTIVE_SCORER_VERSION,
        "proposal_version": None,
        "eligible_dispositions": 0,
        "training_dispositions": 0,
        "holdout_dispositions": 0,
        "training_pairs": 0,
        "incumbent": None,
        "challenger": None,
        "reason": "minimum disposition floor not reached: 0/1",
    }


@pytest.mark.asyncio
async def test_background_retrain_crosses_authentic_floor_and_never_activates(
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-051 defends an authentic floor wake without granting promotion authority."""

    await _reset_proposals(memory_session_factory)
    await _insert_gate(
        memory_session_factory,
        gate=30,
        machine_id="m2z4-verification",
    )
    await _insert_gate(memory_session_factory, gate=31)
    service = _service(
        memory_session_factory,
        min_dispositions=3,
        retrain_signal_stride=2,
    )
    assert await service.retrain_if_due() is None

    await _insert_passive_disposition(memory_session_factory, gate=32)
    restarted = _service(
        memory_session_factory,
        min_dispositions=3,
        retrain_signal_stride=2,
    )
    result = await restarted.retrain_if_due()

    assert result is not None
    assert result.status == "not_better"
    assert result.eligible_dispositions == 3
    async with memory_session_factory() as session:
        runs = (await session.execute(select(LearnerRun))).scalars().all()
        active = (
            (await session.execute(select(ScorerConfigRow).where(ScorerConfigRow.active.is_(True))))
            .scalars()
            .one()
        )
    assert len(runs) == 1
    assert runs[0].trigger == "background"
    assert runs[0].eligible_dispositions == 3
    assert active.version == ACTIVE_SCORER_VERSION


@pytest.mark.asyncio
async def test_real_compaction_worker_persists_background_inactive_winner(
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """SPEC D.2 144/153: a compaction event can propose but never activate a winner."""

    await _reset_proposals(memory_session_factory)
    completed: asyncio.Queue[None] = asyncio.Queue()

    class ObservedLearnerService(LearnerService):
        async def compact(self, trigger) -> RetrainResponse:
            result = await super().compact(trigger)
            await completed.put(None)
            return result

    service = ObservedLearnerService(
        memory_session_factory,
        settings=_settings(min_dispositions=4, win_margin=1.0),
        retrain_signal_stride=2,
    )

    worker = LearnerWorker(service)
    worker.start()
    try:
        assert completed.empty()
        await _insert_gate(memory_session_factory, gate=33)
        await _insert_gate(memory_session_factory, gate=34)
        worker.notify(OptimizationTrigger(event_uid="compaction-event", thread_id=UUID(int=33)))
        await asyncio.wait_for(completed.get(), timeout=2)
    finally:
        await worker.stop()

    async with memory_session_factory() as session:
        run = await session.scalar(select(LearnerRun).where(LearnerRun.trigger == "background"))
    assert run is not None
    assert run.result == "proposed"
    assert run.proposal_version is not None
    async with memory_session_factory() as session:
        proposal = await session.get(ScorerConfigRow, run.proposal_version)
        active = (
            (await session.execute(select(ScorerConfigRow).where(ScorerConfigRow.active.is_(True))))
            .scalars()
            .one()
        )
    assert proposal is not None and proposal.active is False
    assert proposal.params["_learner"]["status"] == "proposed"
    assert active.version == ACTIVE_SCORER_VERSION


@pytest.mark.asyncio
async def test_force_values_basin_yields_visible_measured_inactive_learner_proposal(
    memory_app,
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-035/A-047/A-051 keep control provenance without hiding its learner generation."""

    await _reset_proposals(memory_session_factory)
    seed_injection_id = UUID(int=3500)
    async with memory_session_factory() as session, session.begin():
        session.add(
            InjectionEvent(
                event_uid="01KZ4R35000000000000000000",
                injection_id=seed_injection_id,
                thread_id=UUID(int=3501),
                agent_id="general",
                machine_id="studio-mac",
                principal_id="owner",
                project_key=None,
                agent_kind="general",
                prompt_text="force-values basin",
                scorer_version=ACTIVE_SCORER_VERSION,
                memory_id=UUID(int=3502),
                memory_kind="fact",
                features={
                    "sem": 0.5,
                    "kw": 0.0,
                    "time": 0.0,
                    "proj": 0.0,
                    "freq": 0.0,
                    "hist": 0.0,
                    "_memory": {"label": "Ungraded seed", "body": "Ungraded seed body"},
                    "_prepare": {"model_context_tokens": 8192},
                },
                score=0.21,
                rank=1,
                shown_as="near_miss",
                actor_class="human",
                outcome=None,
                ts=datetime(2026, 8, 3, tzinfo=UTC),
            )
        )
    console = await memory_client.post(
        "/v1/scorer-console/query",
        json={"principal_id": "owner", "thread_id": None, "as_of": "now"},
    )
    assert console.status_code == 200
    values = next(
        item["values"] for item in console.json()["configurations"] if item["status"] == "active"
    )
    values["tau"] = 0.6
    simulation = await memory_client.post(
        "/v1/scorer-simulations",
        json={
            "principal_id": "owner",
            "injection_id": str(seed_injection_id),
            "base_version": ACTIVE_SCORER_VERSION,
            "values": values,
            "slice_parameter_id": "scorer.tau",
        },
    )
    assert simulation.status_code == 200
    force_event_uid = "01KZ4R35000000000000000001"
    forced = await memory_client.post(
        "/v1/scorer-configs",
        json={
            "event_uid": force_event_uid,
            "base_version": ACTIVE_SCORER_VERSION,
            "values": values,
            "simulation_digest": simulation.json()["simulation_digest"],
            "force": True,
            "actor_class": "human",
            "machine_id": "studio-mac",
        },
    )
    assert forced.status_code == 200
    control_version = forced.json()["version"]
    assert forced.json()["status"] == "active"

    await _insert_gate(memory_session_factory, gate=35, scorer_version=control_version)
    await _insert_gate(memory_session_factory, gate=36, scorer_version=control_version)
    memory_app.state.learner_service = _service(memory_session_factory)
    retrained = await memory_client.post("/retrain")
    assert retrained.status_code == 200
    assert retrained.json()["status"] == "proposed"
    proposal_version = retrained.json()["proposal_version"]
    assert proposal_version is not None

    refreshed = await memory_client.post(
        "/v1/scorer-console/query",
        params={"scope": "palace"},
        json={"principal_id": "local", "thread_id": None, "as_of": "now"},
    )
    assert refreshed.status_code == 200
    snapshot = refreshed.json()
    assert [item["version"] for item in snapshot["proposed_versions"]] == [proposal_version]
    accuracy = {item["version"]: item for item in snapshot["accuracy"]}
    assert accuracy[proposal_version]["status"] == "measured"
    assert accuracy[proposal_version]["accuracy_percent"] == "100"
    assert accuracy[proposal_version]["weighted_dispositions"] == "2"
    assert accuracy[proposal_version]["weighted_wrong"] == "0"

    async with memory_session_factory() as session:
        proposal = await session.get(ScorerConfigRow, proposal_version)
        active = (
            (await session.execute(select(ScorerConfigRow).where(ScorerConfigRow.active.is_(True))))
            .scalars()
            .one()
        )
    assert proposal is not None and proposal.active is False
    assert proposal.params["tau"] == 0.6
    assert "_control" not in proposal.params
    assert active.version == control_version
    assert {key: value for key, value in proposal.params.items() if not key.startswith("_")} == {
        key: value for key, value in active.params.items() if not key.startswith("_")
    }


@pytest.mark.asyncio
async def test_not_better_receipt_advances_background_cursor_by_stride(
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-051 prevents a completed no-win fit from hot-looping after every later signal."""

    await _reset_proposals(memory_session_factory)
    service = _service(
        memory_session_factory,
        min_dispositions=4,
        retrain_signal_stride=2,
        win_margin=100.0,
    )
    await _insert_gate(memory_session_factory, gate=40)
    await _insert_gate(memory_session_factory, gate=41)

    first = await service.retrain_if_due()
    repeated = await service.retrain_if_due()
    await _insert_gate(memory_session_factory, gate=42)
    second = await service.retrain_if_due()

    assert first is not None and first.status == "not_better"
    assert repeated is None
    assert second is not None and second.status == "not_better"
    async with memory_session_factory() as session:
        runs = (
            (await session.execute(select(LearnerRun).order_by(LearnerRun.ts, LearnerRun.run_uid)))
            .scalars()
            .all()
        )
    assert [run.eligible_dispositions for run in runs] == [4, 6]


@pytest.mark.asyncio
async def test_background_cursor_uses_monotonic_evidence_not_transaction_timestamp(
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-051 makes the highest completed evidence count authoritative under lock waits."""

    await _reset_proposals(memory_session_factory)
    for gate in (43, 44, 45):
        await _insert_gate(memory_session_factory, gate=gate)
    score = {
        "disagreements": 1,
        "weighted_disagreements": "1",
        "injected_tokens": 1,
    }
    async with memory_session_factory() as session, session.begin():
        session.add_all(
            [
                LearnerRun(
                    run_uid="cursor-six",
                    trigger="background",
                    result="not_better",
                    incumbent_version=ACTIVE_SCORER_VERSION,
                    proposal_version=None,
                    eligible_dispositions=6,
                    training_dispositions=4,
                    holdout_dispositions=2,
                    training_pairs=1,
                    source_boundary="gate-45-positive",
                    incumbent=score,
                    challenger=score,
                    reason="completed after waiting for the learner lock",
                    ts=datetime(2026, 8, 3, tzinfo=UTC),
                ),
                LearnerRun(
                    run_uid="cursor-four",
                    trigger="manual",
                    result="not_better",
                    incumbent_version=ACTIVE_SCORER_VERSION,
                    proposal_version=None,
                    eligible_dispositions=4,
                    training_dispositions=2,
                    holdout_dispositions=2,
                    training_pairs=1,
                    source_boundary="gate-44-positive",
                    incumbent=score,
                    challenger=score,
                    reason="later transaction timestamp with an older evidence count",
                    ts=datetime(2026, 8, 4, tzinfo=UTC),
                ),
            ]
        )

    service = _service(
        memory_session_factory,
        min_dispositions=4,
        retrain_signal_stride=2,
    )
    assert await service.retrain_if_due() is None
    async with memory_session_factory() as session:
        run_count = len((await session.execute(select(LearnerRun))).scalars().all())
    assert run_count == 2


@pytest.mark.asyncio
async def test_manual_receipt_makes_waiting_background_fresh_noop(
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-051 serializes a waiting due check before it takes its repeatable-read snapshot."""

    await _reset_proposals(memory_session_factory)
    await _insert_gate(memory_session_factory, gate=70)
    await _insert_gate(memory_session_factory, gate=71)
    service = _service(
        memory_session_factory,
        min_dispositions=4,
        retrain_signal_stride=2,
    )

    async with _held_learner_lock(memory_session_factory):
        manual_task = asyncio.create_task(service.retrain())
        await _wait_for_learner_waiters(memory_session_factory, 1)
        background_task = asyncio.create_task(service.retrain_if_due())
        await _wait_for_learner_waiters(memory_session_factory, 2)

    manual, background = await asyncio.gather(manual_task, background_task)

    assert manual.status == "proposed"
    assert background is None
    async with memory_session_factory() as session:
        runs = (await session.execute(select(LearnerRun))).scalars().all()
        proposals = (
            (
                await session.execute(
                    select(ScorerConfigRow).where(
                        ScorerConfigRow.version.not_in(
                            (
                                "v0",
                                "m3f-location-v1",
                                "m3ti-thread-v1",
                                ACTIVE_SCORER_VERSION,
                            )
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        active = (
            (await session.execute(select(ScorerConfigRow).where(ScorerConfigRow.active.is_(True))))
            .scalars()
            .one()
        )
    assert [(run.trigger, run.result, run.eligible_dispositions) for run in runs] == [
        ("manual", "proposed", 4)
    ]
    assert [proposal.version for proposal in proposals] == [manual.proposal_version]
    assert proposals[0].active is False
    assert active.version == ACTIVE_SCORER_VERSION


@pytest.mark.asyncio
async def test_competing_backgrounds_at_one_boundary_fit_once(
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-051 permits exactly one receipt and proposal for simultaneous due checks."""

    await _reset_proposals(memory_session_factory)
    await _insert_gate(memory_session_factory, gate=72)
    await _insert_gate(memory_session_factory, gate=73)
    service = _service(
        memory_session_factory,
        min_dispositions=4,
        retrain_signal_stride=2,
    )

    async with _held_learner_lock(memory_session_factory):
        first_task = asyncio.create_task(service.retrain_if_due())
        await _wait_for_learner_waiters(memory_session_factory, 1)
        second_task = asyncio.create_task(service.retrain_if_due())
        await _wait_for_learner_waiters(memory_session_factory, 2)

    first, second = await asyncio.gather(first_task, second_task)

    assert first is not None and first.status == "proposed"
    assert second is None
    async with memory_session_factory() as session:
        runs = (await session.execute(select(LearnerRun))).scalars().all()
        optimization_runs = (await session.execute(select(OptimizationRun))).scalars().all()
        proposals = (
            (
                await session.execute(
                    select(ScorerConfigRow).where(
                        ScorerConfigRow.version.not_in(
                            (
                                "v0",
                                "m3f-location-v1",
                                "m3ti-thread-v1",
                                ACTIVE_SCORER_VERSION,
                            )
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        active = (
            (await session.execute(select(ScorerConfigRow).where(ScorerConfigRow.active.is_(True))))
            .scalars()
            .one()
        )
    assert [(run.trigger, run.result, run.eligible_dispositions) for run in runs] == [
        ("background", "proposed", 4)
    ]
    assert len(optimization_runs) == 1
    assert [proposal.version for proposal in proposals] == [first.proposal_version]
    assert proposals[0].active is False
    assert active.version == ACTIVE_SCORER_VERSION


@pytest.mark.asyncio
async def test_learner_lock_is_released_after_snapshot_failure(
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-051 keeps later work live when a serialized learner check raises."""

    class FailingLearnerService(LearnerService):
        async def _retrain_in_snapshot(
            self,
            session: AsyncSession,
            *,
            trigger: Literal["manual", "background"],
            due_only: bool,
            optimization_trigger: OptimizationTrigger,
            started_at: datetime,
        ) -> RetrainResponse | None:
            del session, trigger, due_only, optimization_trigger, started_at
            raise RuntimeError("deterministic learner failure")

    failing = FailingLearnerService(
        memory_session_factory,
        settings=_settings(min_dispositions=4, win_margin=1.0),
        retrain_signal_stride=2,
    )
    with pytest.raises(RuntimeError, match="deterministic learner failure"):
        await failing.retrain()

    assert await _learner_lock_count(memory_session_factory) == 0
    recovered = await asyncio.wait_for(
        _service(memory_session_factory).retrain_if_due(),
        timeout=2,
    )
    assert recovered is None
    assert await _learner_lock_count(memory_session_factory) == 0
    async with memory_session_factory() as session:
        runs = (await session.execute(select(LearnerRun))).scalars().all()
    assert runs == []


@pytest.mark.asyncio
async def test_manual_retrain_below_floor_does_not_delay_floor_and_above_floor_resets_stride(
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-051 keeps FORCE RETRAIN and automatic cadence on one durable evidence boundary."""

    await _reset_proposals(memory_session_factory)
    service = _service(
        memory_session_factory,
        min_dispositions=4,
        retrain_signal_stride=4,
    )
    await _insert_gate(memory_session_factory, gate=50)
    below_floor = await service.retrain()
    await _insert_gate(memory_session_factory, gate=51)
    at_floor = await service.retrain_if_due()
    await _insert_gate(memory_session_factory, gate=52)
    manual_reset = await service.retrain()
    await _insert_gate(memory_session_factory, gate=53)
    before_stride = await service.retrain_if_due()
    await _insert_gate(memory_session_factory, gate=54)
    after_stride = await service.retrain_if_due()

    assert below_floor.status == "insufficient_data"
    assert at_floor is not None and at_floor.eligible_dispositions == 4
    assert manual_reset.eligible_dispositions == 6
    assert before_stride is None
    assert after_stride is not None and after_stride.eligible_dispositions == 10
    async with memory_session_factory() as session:
        runs = (
            (await session.execute(select(LearnerRun).order_by(LearnerRun.ts, LearnerRun.run_uid)))
            .scalars()
            .all()
        )
    assert [run.trigger for run in runs] == ["manual", "background", "manual", "background"]
    assert [run.eligible_dispositions for run in runs] == [2, 4, 6, 10]


@pytest.mark.asyncio
async def test_retrain_accepts_both_pre_a051_proposal_manifest_variants(
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-031/A-051 preserve content-addressed replay across added holdout-weight evidence."""

    await _reset_proposals(memory_session_factory)
    service = _service(memory_session_factory)
    await _insert_gate(memory_session_factory, gate=60)
    await _insert_gate(memory_session_factory, gate=61)
    first = await service.retrain()
    assert first.proposal_version is not None

    async with memory_session_factory() as session, session.begin():
        proposal = await session.get(ScorerConfigRow, first.proposal_version)
        assert proposal is not None
        params = dict(proposal.params)
        learner = dict(params["_learner"])
        learner.pop("holdout_weight")
        params["_learner"] = learner
        proposal.params = params
    assert (await service.retrain()).proposal_version == first.proposal_version

    async with memory_session_factory() as session, session.begin():
        proposal = await session.get(ScorerConfigRow, first.proposal_version)
        assert proposal is not None
        params = dict(proposal.params)
        learner = dict(params["_learner"])
        learner.pop("holdout_dispositions")
        params["_learner"] = learner
        proposal.params = params
    assert (await service.retrain()).proposal_version == first.proposal_version


@pytest.mark.asyncio
async def test_learner_run_receipts_are_database_enforced_append_only(
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-051 keeps completed-run cadence evidence durable by rejecting mutation and deletion."""

    await _reset_proposals(memory_session_factory)
    service = _service(memory_session_factory, min_dispositions=4)
    await service.retrain()

    with pytest.raises(DBAPIError, match="learner_run is append-only"):
        async with memory_session_factory() as session, session.begin():
            await session.execute(update(LearnerRun).values(reason="rewritten"))
    with pytest.raises(DBAPIError, match="learner_run is append-only"):
        async with memory_session_factory() as session, session.begin():
            await session.execute(delete(LearnerRun))
    with pytest.raises(DBAPIError, match="optimization history is append-only"):
        async with memory_session_factory() as session, session.begin():
            await session.execute(update(OptimizationRun).values(verdict="rewritten"))
    with pytest.raises(DBAPIError, match="optimization history is append-only"):
        async with memory_session_factory() as session, session.begin():
            await session.execute(delete(OptimizationRun))

    async with memory_session_factory() as session:
        runs = (await session.execute(select(LearnerRun))).scalars().all()
    assert len(runs) == 1
    assert runs[0].trigger == "manual"
