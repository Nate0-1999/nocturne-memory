"""Live-Postgres proofs for A-035 graph and scorer-console authority."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from conftest import ACTIVE_SCORER_VERSION, basis_vector, vector_with_cosine
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from spine.db.models import (
    InjectionEvent,
    InjectionEventAnnotation,
    LearnerRun,
    MemoryEdge,
    MemoryRevision,
    MemoryUnit,
)
from spine.db.models import ScorerActivation as ScorerActivationRow
from spine.db.models import ScorerConfig as ScorerConfigRow
from spine.m2k.contracts import ScorerSimulationResponse, ScorerValues
from spine.m2k.service import _CONTROL_ADVISORY_LOCK_KEY, M2KService


@asynccontextmanager
async def _held_control_lock(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    bind = session_factory.kw.get("bind")
    assert isinstance(bind, AsyncEngine)
    connection = await bind.connect()
    try:
        await connection.execute(
            text("SELECT pg_advisory_lock(:key)"),
            {"key": _CONTROL_ADVISORY_LOCK_KEY},
        )
        await connection.commit()
        yield
    finally:
        if connection.in_transaction():
            await connection.rollback()
        released = await connection.scalar(
            text("SELECT pg_advisory_unlock(:key)"),
            {"key": _CONTROL_ADVISORY_LOCK_KEY},
        )
        await connection.commit()
        await connection.close()
        assert released is True


async def _wait_for_control_lock_waiters(
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
                {"key": _CONTROL_ADVISORY_LOCK_KEY},
            )
        if count is not None and count >= expected:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"expected {expected} M2K control-lock waiters")


async def _insert_graph_fixture(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[UUID, UUID]:
    first_id = UUID(int=9101)
    second_id = UUID(int=9102)
    now = datetime(2026, 8, 3, 12, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add_all(
            [
                MemoryUnit(
                    id=first_id,
                    principal_id="owner",
                    label="Graph one",
                    body="First graph memory",
                    kind="fact",
                    keywords=["graph"],
                    embedding=basis_vector(0),
                    embedding_model="test-embedding-1536",
                    project_key=None,
                    thread_origin=None,
                    origin_path=None,
                    pin=True,
                    status="active",
                    revision=2,
                    stats={
                        "injections": 9,
                        "removals": 0,
                        "citations": 0,
                        "never_kills": 0,
                        "last_injected_at": None,
                    },
                    bias=0.0,
                    created_at=now,
                    updated_at=now + timedelta(minutes=2),
                ),
                MemoryUnit(
                    id=second_id,
                    principal_id="owner",
                    label="Graph two",
                    body="Second graph memory",
                    kind="preference",
                    keywords=["graph"],
                    embedding=vector_with_cosine(0.8),
                    embedding_model="test-embedding-1536",
                    project_key=None,
                    thread_origin=None,
                    origin_path=None,
                    pin=False,
                    status="quarantined",
                    revision=1,
                    stats={
                        "injections": 2,
                        "removals": 1,
                        "citations": 0,
                        "never_kills": 3,
                        "last_injected_at": None,
                    },
                    bias=-0.1,
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                MemoryRevision(
                    rev_uid="01KZ4R00000000000000000001",
                    parent_uid=None,
                    memory_id=first_id,
                    revision=1,
                    body="Original graph memory",
                    label="Graph one",
                    editor="user",
                    origin_machine_id="studio",
                    reason="create",
                    ts=now,
                ),
                MemoryRevision(
                    rev_uid="01KZ4R00000000000000000002",
                    parent_uid="01KZ4R00000000000000000001",
                    memory_id=first_id,
                    revision=2,
                    body="First graph memory",
                    label="Graph one",
                    editor="user",
                    origin_machine_id="studio",
                    reason="panel/edit",
                    ts=now + timedelta(minutes=2),
                ),
                MemoryRevision(
                    rev_uid="01KZ4R00000000000000000003",
                    parent_uid=None,
                    memory_id=second_id,
                    revision=1,
                    body="Second graph memory",
                    label="Graph two",
                    editor="user",
                    origin_machine_id="studio",
                    reason="create",
                    ts=now,
                ),
                MemoryEdge(
                    edge_uid="01KZ4R00000000000000000004",
                    from_memory_id=first_id,
                    to_memory_id=second_id,
                    edge_type="contradicts",
                    created_at=now,
                ),
            ]
        )
    return first_id, second_id


@pytest.mark.asyncio
async def test_memory_graph_uses_exact_encodings_and_current_membership(
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-035 is defended by verifying that memory graph uses exact encodings and current
    membership; this prevents drift in the authoritative graph, console, and accuracy
    boundary.
    """
    first_id, second_id = await _insert_graph_fixture(memory_session_factory)

    global_response = await memory_client.post(
        "/v1/memory-graph/query",
        json={"principal_id": "owner", "memory_ids": None},
    )
    current_response = await memory_client.post(
        "/v1/memory-graph/query",
        json={
            "principal_id": "owner",
            "memory_ids": [str(first_id), str(UUID(int=9999))],
        },
    )

    assert global_response.status_code == 200
    graph = global_response.json()
    assert graph["graph_edge_sim"] == 0.75
    assert [node["memory"]["memory_id"] for node in graph["nodes"]] == [
        str(first_id),
        str(second_id),
    ]
    first = graph["nodes"][0]
    assert first["memory"]["stats"]["injections"] == 9
    assert first["memory"]["pin"] is True
    assert [revision["revision"] for revision in first["revisions"]] == [1, 2]
    assert [revision["body"] for revision in first["revisions"]] == [
        "Original graph memory",
        "First graph memory",
    ]
    assert all(revision["label"] == "Graph one" for revision in first["revisions"])
    assert {
        (edge["kind"], edge["edge_type"], edge["revision_count"]) for edge in graph["edges"]
    } >= {
        ("similarity", None, None),
        ("lineage", "contradicts", None),
        ("edit_trail", None, 2),
    }
    assert current_response.status_code == 200
    current = current_response.json()
    assert len(current["nodes"]) == 1
    assert current["nodes"][0]["in_current_context"] is True
    assert current["omitted_memory_ids"] == [str(UUID(int=9999))]


@pytest.mark.asyncio
async def test_console_contributions_sum_exactly_and_control_inserts_a_version(
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A-035/A-047 are defended by proving that contribution math, replay previews,
    content-addressed force, and the immutable activation journal share one authority.
    """
    original_deep_receipt = M2KService._deep_receipt
    observed_isolation: list[str] = []

    async def observe_deep_receipt(
        service: M2KService,
        session: AsyncSession,
        base: ScorerConfigRow,
        values: ScorerValues,
    ) -> ScorerSimulationResponse:
        isolation = await session.scalar(text("SHOW transaction_isolation"))
        assert isinstance(isolation, str)
        observed_isolation.append(isolation)
        return await original_deep_receipt(service, session, base, values)

    monkeypatch.setattr(M2KService, "_deep_receipt", observe_deep_receipt)
    thread_id = UUID(int=9201)
    memory_id = UUID(int=9202)
    async with memory_session_factory() as session, session.begin():
        session.add(
            InjectionEvent(
                event_uid="01KZ4R10000000000000000001",
                injection_id=UUID(int=9203),
                thread_id=thread_id,
                agent_id="general",
                machine_id="studio",
                principal_id="owner",
                project_key=None,
                agent_kind="general",
                prompt_text="console fixture",
                scorer_version=ACTIVE_SCORER_VERSION,
                memory_id=memory_id,
                memory_kind="fact",
                features={
                    "sem": 0.5,
                    "kw": 0.25,
                    "time": 1.0,
                    "proj": 0.5,
                    "freq": 0.0,
                    "hist": 0.5,
                    "_memory": {"label": "Console memory", "body": "Console body"},
                    "_prepare": {"model_context_tokens": 8192},
                },
                score=0.4,
                rank=1,
                shown_as="injected",
                actor_class="human",
                outcome="kept",
                ts=datetime(2026, 8, 3, 14, tzinfo=UTC),
            )
        )

    console = await memory_client.post(
        "/v1/scorer-console/query",
        json={
            "principal_id": "owner",
            "thread_id": str(thread_id),
            "as_of": "now",
        },
    )
    assert console.status_code == 200
    payload = console.json()
    assert payload["scope"] == "CURRENT"
    point = payload["candidates"][0]["points"][0]
    total = sum(Decimal(value) for value in point["contributions"].values() if value is not None)
    assert total == Decimal(point["score"])
    assert len(payload["descriptors"]) == 10
    assert "scorer.top_k" not in {item["id"] for item in payload["descriptors"]}

    values = payload["configurations"][0]["values"]
    values["tau"] = 0.6
    simulation = await memory_client.post(
        "/v1/scorer-simulations",
        json={
            "principal_id": "owner",
            "injection_id": str(UUID(int=9203)),
            "base_version": ACTIVE_SCORER_VERSION,
            "values": values,
            "slice_parameter_id": "scorer.top_k",
        },
    )
    assert simulation.status_code == 200
    receipt = simulation.json()
    assert receipt["instant"]["status"] == "ready"
    assert len(receipt["slice"]["points"]) == 8
    event_uid = "01KZ4R10000000000000000002"
    request = {
        "event_uid": event_uid,
        "base_version": ACTIVE_SCORER_VERSION,
        "values": values,
        "simulation_digest": receipt["simulation_digest"],
        "force": True,
        "actor_class": "human",
        "machine_id": "studio",
    }
    stale = await memory_client.post(
        "/v1/scorer-configs",
        json={**request, "simulation_digest": "0" * 64},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "Scorer operation refused: simulation_stale."
    observed_isolation.clear()
    created = await memory_client.post("/v1/scorer-configs", json=request)
    assert observed_isolation == ["repeatable read"]
    observed_isolation.clear()
    replay = await memory_client.post("/v1/scorer-configs", json=request)

    assert observed_isolation == []
    assert created.status_code == 200
    assert replay.status_code == 200
    assert created.json()["version"] == f"m2k-{event_uid}"
    assert created.json()["status"] == "active"
    async with memory_session_factory() as session:
        active = (
            (await session.execute(select(ScorerConfigRow).where(ScorerConfigRow.active.is_(True))))
            .scalars()
            .one()
        )
        activations = (await session.execute(select(ScorerActivationRow))).scalars().all()
    assert active.version == f"m2k-{event_uid}"
    assert len(activations) == 1
    assert activations[0].changes["scorer.tau"] == {"old": 0.55, "new": 0.6}
    assert activations[0].changes["_force"] == {
        "simulation_digest": receipt["simulation_digest"],
        "source_boundary": "01KZ4R10000000000000000001",
        "holdout_dispositions": 0,
        "incumbent_accuracy_percent": None,
        "accuracy_percent": None,
        "delta_percent": None,
    }
    refreshed = await memory_client.post(
        "/v1/scorer-console/query",
        params={"scope": "palace"},
        json={"principal_id": "local", "thread_id": str(thread_id), "as_of": "now"},
    )
    assert refreshed.status_code == 200
    assert [item["kind"] for item in refreshed.json()["learning"]["annotations"]] == [
        "force_values"
    ]


@pytest.mark.asyncio
async def test_competing_force_values_take_a_fresh_snapshot_after_the_control_lock(
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-047/A-053 serialize FORCE before its repeatable-read evidence snapshot."""

    console = await memory_client.post(
        "/v1/scorer-console/query",
        json={"principal_id": "owner", "thread_id": None, "as_of": "now"},
    )
    assert console.status_code == 200
    values = console.json()["configurations"][0]["values"]
    values["tau"] = 0.6
    simulation = await memory_client.post(
        "/v1/scorer-simulations",
        json={
            "principal_id": "owner",
            "injection_id": None,
            "base_version": ACTIVE_SCORER_VERSION,
            "values": values,
            "slice_parameter_id": "scorer.top_k",
        },
    )
    assert simulation.status_code == 200
    base_request = {
        "base_version": ACTIVE_SCORER_VERSION,
        "values": values,
        "simulation_digest": simulation.json()["simulation_digest"],
        "force": True,
        "actor_class": "human",
        "machine_id": "studio",
    }
    async with _held_control_lock(memory_session_factory):
        requests = [
            asyncio.create_task(
                memory_client.post(
                    "/v1/scorer-configs",
                    json={
                        **base_request,
                        "event_uid": f"01KZ4R1000000000000000001{index}",
                    },
                )
            )
            for index in range(2)
        ]
        await _wait_for_control_lock_waiters(memory_session_factory, 2)

    responses = await asyncio.gather(*requests)

    assert sorted(response.status_code for response in responses) == [200, 409]
    stale = next(response for response in responses if response.status_code == 409)
    created = next(response for response in responses if response.status_code == 200)
    assert stale.json()["detail"] == "Scorer operation refused: stale_base."
    async with memory_session_factory() as session:
        configs = (
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
        activations = (await session.execute(select(ScorerActivationRow))).scalars().all()
        active = (
            (await session.execute(select(ScorerConfigRow).where(ScorerConfigRow.active.is_(True))))
            .scalars()
            .one()
        )
        control_locks = await session.scalar(
            text(
                "SELECT count(*) FROM pg_locks "
                "WHERE locktype = 'advisory' AND classid = 0 "
                "AND objid = :key AND granted"
            ),
            {"key": _CONTROL_ADVISORY_LOCK_KEY},
        )
    assert [row.version for row in configs] == [created.json()["version"]]
    assert [row.version for row in activations] == [created.json()["version"]]
    assert active.version == created.json()["version"]
    assert control_locks == 0


@pytest.mark.asyncio
async def test_console_learning_view_is_one_exact_server_authored_scoreboard(
    memory_app,
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-031/A-051 keep cadence, hygiene, weighted agreement, and receipts in one read model."""

    memory_app.state.m2k_service = M2KService(
        memory_session_factory,
        graph_edge_sim=0.75,
        passive_discount=0.25,
        learner_min_dispositions=3,
        retrain_signal_stride=2,
    )
    started = datetime(2026, 8, 3, 16, tzinfo=UTC)

    def event(
        *,
        seed: int,
        actor_class: str,
        outcome: str,
        shown_as: str,
        machine_id: str = "studio",
    ) -> InjectionEvent:
        return InjectionEvent(
            event_uid=f"01KZ4R3{seed:019d}",
            injection_id=UUID(int=9400 + seed),
            thread_id=UUID(int=9500 + seed),
            agent_id="general",
            machine_id=machine_id,
            principal_id="owner",
            project_key=None,
            agent_kind="general",
            prompt_text="learning read model fixture",
            scorer_version=ACTIVE_SCORER_VERSION,
            memory_id=UUID(int=9600 + seed),
            memory_kind="fact",
            features={
                "sem": 1.0,
                "kw": 1.0,
                "time": 0.0,
                "proj": 0.0,
                "freq": 0.0,
                "hist": 0.0,
                "_memory": {"label": f"Evidence {seed}", "body": f"Body {seed}"},
            },
            score=0.58,
            rank=1,
            shown_as=shown_as,
            actor_class=actor_class,
            outcome=outcome,
            ts=started + timedelta(minutes=seed),
        )

    async with memory_session_factory() as session, session.begin():
        session.add_all(
            [
                event(seed=1, actor_class="human", outcome="kept", shown_as="injected"),
                event(
                    seed=2,
                    actor_class="human",
                    outcome="removed:not_relevant",
                    shown_as="injected",
                ),
                event(
                    seed=3,
                    actor_class="passive",
                    outcome="auto_entered",
                    shown_as="injected",
                ),
                event(
                    seed=4,
                    actor_class="human",
                    outcome="kept",
                    shown_as="injected",
                    machine_id="m2z4-verification",
                ),
                LearnerRun(
                    run_uid="01KZ4R30000000000000000009",
                    trigger="background",
                    result="not_better",
                    incumbent_version=ACTIVE_SCORER_VERSION,
                    proposal_version=None,
                    eligible_dispositions=3,
                    training_dispositions=2,
                    holdout_dispositions=1,
                    training_pairs=1,
                    source_boundary="01KZ4R3000000000000000003",
                    incumbent={
                        "disagreements": 1,
                        "weighted_disagreements": "1",
                        "injected_tokens": 2,
                    },
                    challenger={
                        "disagreements": 1,
                        "weighted_disagreements": "1",
                        "injected_tokens": 2,
                    },
                    reason="challenger did not clear the replay win rule",
                    ts=started + timedelta(minutes=5),
                ),
            ]
        )

    memory_app.state.settings.owner_principal_id = "owner"
    response = await memory_client.post(
        "/v1/scorer-console/query",
        params={"scope": "palace"},
        json={"principal_id": "owner", "thread_id": None, "as_of": "now"},
    )

    assert response.status_code == 200
    learning = response.json()["learning"]
    assert {
        key: learning[key]
        for key in (
            "eligible_dispositions",
            "hygiene_excluded_dispositions",
            "minimum_dispositions",
            "remaining_to_floor",
            "floor_met",
            "retrain_signal_stride",
            "evaluated_through",
            "signals_since_last_run",
            "signals_until_next_run",
            "active_scorer_version",
            "right",
            "wrong",
            "weighted_right",
            "weighted_wrong",
            "weighted_agreement_percent",
        )
    } == {
        "eligible_dispositions": 3,
        "hygiene_excluded_dispositions": 1,
        "minimum_dispositions": 3,
        "remaining_to_floor": 0,
        "floor_met": True,
        "retrain_signal_stride": 2,
        "evaluated_through": 3,
        "signals_since_last_run": 0,
        "signals_until_next_run": 2,
        "active_scorer_version": ACTIVE_SCORER_VERSION,
        "right": 2,
        "wrong": 1,
        "weighted_right": "1.25",
        "weighted_wrong": "1",
        "weighted_agreement_percent": "55.55555555555555555555555556",
    }
    assert [
        (
            point["right"],
            point["wrong"],
            point["weighted_right"],
            point["weighted_wrong"],
            point["weighted_agreement_percent"],
        )
        for point in learning["live_agreement"]
    ] == [
        (1, 0, "1", "0", "100"),
        (1, 1, "1", "1", "50"),
        (1, 1, "0.25", "1", "20"),
    ]
    assert learning["retrain_runs"][0]["result"] == "not_better"
    assert learning["retrain_runs"][0]["incumbent"]["weighted_disagreements"] == "1"
    assert learning["annotations"] == [
        {
            "kind": "retrain",
            "event_uid": "01KZ4R30000000000000000009",
            "ts": (started + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
            "version": ACTIVE_SCORER_VERSION,
            "result": "not_better",
        }
    ]

    # PLAN M3SC / F100: a principal gets only its signals, never global training history.
    for principal, expected_signals in [("owner", 3), ("nocturne-verification-unrelated", 0)]:
        scoped = await memory_client.post(
            "/v1/scorer-console/query",
            json={"principal_id": principal, "thread_id": None},
        )
        assert scoped.status_code == 200
        payload = scoped.json()
        assert payload["metrics_scope"] == "principal"
        assert payload["learning"]["eligible_dispositions"] == expected_signals
        assert payload["learning"]["retrain_runs"] == []
        assert payload["learning"]["annotations"] == []
        assert payload["accuracy"] == payload["activations"] == payload["proposed_versions"] == []
        assert all(config["replay"] is None for config in payload["configurations"])
        if expected_signals == 0:
            assert payload["learning"]["live_agreement"] == payload["candidates"] == []
    refused = await memory_client.post(
        "/v1/scorer-console/query",
        params={"scope": "palace"},
        json={"principal_id": "nocturne-verification-unrelated", "thread_id": None},
    )
    assert refused.status_code == 403
    assert refused.json()["detail"] == "Only the Palace owner can view the whole Palace."


@pytest.mark.asyncio
async def test_f033_production_legacy_aliases_render_the_honest_owner_scoreboard(
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-053/F033 replay the exact legacy aliases that corrected 87 exclusions to 90."""

    started = datetime(2026, 8, 10, 16, tzinfo=UTC)
    identities = (
        (
            "01KY2JE3JKY1MXYCKVZ93KY399",
            "d1-4f6500c7-336f-4ce4-871b-9f31ef770f9f",
            "d1-relay",
            "kept",
        ),
        (
            "01KY54AX6YSYMXA4X7V1KZHH2A",
            "local",
            "local-machine",
            "removed:not_relevant",
        ),
        (
            "01KZD7697HE5ST1HMT7AGWRZK3",
            "nocturne-deploy-verify-ea5a431ef134474881a7f046bb52982e",
            "nocturne-deploy",
            "kept",
        ),
        (
            "01KZP578SD8RKF110T2FHV79NW",
            "nocturne-deploy-verify-5a9c7bada69b4629917630fcde814a55",
            "nocturne-deploy",
            "kept",
        ),
    )
    prior_hygiene_rows = [
        InjectionEvent(
            event_uid=f"01KZ6H{index:020d}",
            injection_id=UUID(int=9600 + index),
            thread_id=UUID(int=10600 + index),
            agent_id="general",
            machine_id="m2xs-sop-verification",
            principal_id="m2xs-sop-verification",
            project_key=None,
            agent_kind="general",
            prompt_text="Prior production verification hygiene",
            scorer_version=ACTIVE_SCORER_VERSION,
            memory_id=UUID(int=11600 + index),
            memory_kind="fact",
            features={
                "sem": 1.0,
                "kw": 1.0,
                "time": 0.0,
                "proj": 0.0,
                "freq": 0.0,
                "hist": 0.0,
                "_memory": {
                    "label": f"Prior verification {index}",
                    "body": f"Prior verification body {index}",
                },
            },
            score=0.58,
            rank=1,
            shown_as="injected",
            actor_class="human",
            outcome="kept",
            ts=started - timedelta(days=1) + timedelta(minutes=index),
        )
        for index in range(1, 88)
    ]
    rows: list[InjectionEvent] = []
    for index, (event_uid, principal_id, machine_id, outcome) in enumerate(identities, start=1):
        rows.append(
            InjectionEvent(
                event_uid=event_uid,
                injection_id=UUID(int=9700 + index),
                thread_id=UUID(int=9800 + index),
                agent_id="general",
                machine_id=machine_id,
                principal_id=principal_id,
                project_key=None,
                agent_kind="general",
                prompt_text="F033 production correlation",
                scorer_version=ACTIVE_SCORER_VERSION,
                memory_id=UUID(int=9900 + index),
                memory_kind="fact",
                features={
                    "sem": 1.0,
                    "kw": 1.0,
                    "time": 0.0,
                    "proj": 0.0,
                    "freq": 0.0,
                    "hist": 0.0,
                    "_memory": {"label": f"F033 {index}", "body": f"Body {index}"},
                },
                score=0.58,
                rank=1,
                shown_as="injected",
                actor_class="human",
                outcome=outcome,
                ts=started + timedelta(minutes=index),
            )
        )
    owner_row = rows[1]
    verification_rows = [rows[index] for index in (0, 2, 3)]
    async with memory_session_factory() as session, session.begin():
        session.add_all([*prior_hygiene_rows, owner_row])

    before = await memory_client.post(
        "/v1/scorer-console/query",
        params={"scope": "palace"},
        json={"principal_id": "local", "thread_id": None, "as_of": "now"},
    )
    assert before.status_code == 200
    assert before.json()["learning"]["eligible_dispositions"] == 1
    assert before.json()["learning"]["hygiene_excluded_dispositions"] == 87

    async with memory_session_factory() as session, session.begin():
        session.add_all(verification_rows)

    response = await memory_client.post(
        "/v1/scorer-console/query",
        params={"scope": "palace"},
        json={"principal_id": "local", "thread_id": None, "as_of": "now"},
    )

    assert response.status_code == 200
    payload = response.json()
    learning = payload["learning"]
    assert learning["minimum_dispositions"] == 25
    assert learning["eligible_dispositions"] == 1
    assert learning["hygiene_excluded_dispositions"] == 90
    assert learning["remaining_to_floor"] == 24
    assert learning["floor_met"] is False
    assert learning["right"] == 0
    assert learning["wrong"] == 1
    assert learning["weighted_right"] == "0"
    assert learning["weighted_wrong"] == "1"
    assert learning["weighted_agreement_percent"] == "0"
    assert [point["event_uid"] for point in learning["live_agreement"]] == [
        "01KY54AX6YSYMXA4X7V1KZHH2A"
    ]
    assert payload["active_version"] == ACTIVE_SCORER_VERSION
    assert payload["proposed_versions"] == []


@pytest.mark.asyncio
async def test_console_and_deep_simulation_use_the_annotation_aware_evidence_projection(
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-053/F033 give Console and deep receipts one annotated whole-gate projection."""

    started = datetime(2026, 8, 10, 18, tzinfo=UTC)

    def event(seed: int, *, gate: int, outcome: str, shown_as: str) -> InjectionEvent:
        return InjectionEvent(
            event_uid=f"01KZ5R{seed:020d}",
            injection_id=UUID(int=gate),
            thread_id=UUID(int=10000 + gate),
            agent_id="general",
            machine_id="studio-mac",
            principal_id="owner",
            project_key=None,
            agent_kind="general",
            prompt_text="deep annotation fixture",
            scorer_version=ACTIVE_SCORER_VERSION,
            memory_id=UUID(int=10100 + seed),
            memory_kind="fact",
            features={
                "sem": float(seed % 2),
                "kw": 0.0,
                "time": 0.0,
                "proj": 0.0,
                "freq": 0.0,
                "hist": 0.0,
                "_memory": {"label": f"Deep {seed}", "body": f"Deep body {seed}"},
            },
            score=0.42 if seed % 2 else 0.0,
            rank=1,
            shown_as=shown_as,
            actor_class="human",
            outcome=outcome,
            ts=started + timedelta(hours=gate),
        )

    rows = [
        event(1, gate=10201, outcome="added_back", shown_as="near_miss"),
        event(2, gate=10201, outcome="removed:not_relevant", shown_as="injected"),
        event(3, gate=10202, outcome="added_back", shown_as="near_miss"),
        event(4, gate=10202, outcome="removed:not_relevant", shown_as="injected"),
    ]
    async with memory_session_factory() as session, session.begin():
        session.add_all(rows)
    console = await memory_client.post(
        "/v1/scorer-console/query",
        json={"principal_id": "owner", "thread_id": None, "as_of": "now"},
    )
    assert console.status_code == 200
    before_learning = console.json()["learning"]
    assert before_learning["eligible_dispositions"] == 4
    assert before_learning["hygiene_excluded_dispositions"] == 0
    assert [point["event_uid"] for point in before_learning["live_agreement"]] == [
        "01KZ5R00000000000000000001",
        "01KZ5R00000000000000000002",
        "01KZ5R00000000000000000003",
        "01KZ5R00000000000000000004",
    ]
    values = console.json()["configurations"][0]["values"]
    request = {
        "principal_id": "owner",
        "injection_id": None,
        "base_version": ACTIVE_SCORER_VERSION,
        "values": values,
        "slice_parameter_id": "scorer.top_k",
    }
    before = await memory_client.post("/v1/scorer-simulations", json=request)
    assert before.status_code == 200
    assert before.json()["source_boundary"] == "01KZ5R00000000000000000004"
    assert before.json()["holdout_dispositions"] == 2

    async with memory_session_factory() as session, session.begin():
        session.add(
            InjectionEventAnnotation(
                target_event_uid="01KZ5R00000000000000000003",
                kind="verification_only",
                target_principal_id="owner",
                target_machine_id="studio-mac",
                reason="F033 deep projection",
                annotator_principal_id="m2za-sop-verification",
                annotator_machine_id="m2za-sop-verification",
                annotator_origin_agent="verification:m2za",
            )
        )
    after = await memory_client.post("/v1/scorer-simulations", json=request)
    console_after = await memory_client.post(
        "/v1/scorer-console/query",
        json={"principal_id": "owner", "thread_id": None, "as_of": "now"},
    )

    assert after.status_code == 200
    assert after.json()["source_boundary"] == "01KZ5R00000000000000000002"
    assert after.json()["holdout_dispositions"] == 0
    assert after.json()["simulation_digest"] != before.json()["simulation_digest"]
    assert console_after.status_code == 200
    after_learning = console_after.json()["learning"]
    assert after_learning["eligible_dispositions"] == 2
    assert after_learning["hygiene_excluded_dispositions"] == 2
    assert [point["event_uid"] for point in after_learning["live_agreement"]] == [
        "01KZ5R00000000000000000001",
        "01KZ5R00000000000000000002",
    ]


@pytest.mark.asyncio
async def test_only_learner_proposals_can_be_activated_and_accuracy_is_measured(
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-035/A-047 are defended by proving that audition is read-only and only a
    subsequent explicit activation can replace the incumbent scorer.
    """
    injection_id = UUID(int=9301)
    memory_id = UUID(int=9302)
    async with memory_session_factory() as session, session.begin():
        base = await session.get(ScorerConfigRow, ACTIVE_SCORER_VERSION)
        assert base is not None
        params = dict(base.params)
        params["_learner"] = {
            "status": "proposed",
            "holdout_dispositions": 10,
            "holdout_weight": "5.5",
            "replay": {
                "incumbent": {
                    "disagreements": 3,
                    "weighted_disagreements": "2",
                    "injected_tokens": 100,
                },
                "challenger": {
                    "disagreements": 1,
                    "weighted_disagreements": "0.5",
                    "injected_tokens": 100,
                },
            },
        }
        session.add(
            ScorerConfigRow(
                version="learner-proposal",
                weights=dict(base.weights),
                params=params,
                active=False,
            )
        )
        legacy_params = dict(base.params)
        legacy_params["_learner"] = {
            "status": "proposed",
            "holdout_dispositions": 10,
            "replay": {
                "challenger": {
                    "disagreements": 1,
                    "weighted_disagreements": "0.5",
                }
            },
        }
        session.add(
            ScorerConfigRow(
                version="legacy-proposal",
                weights=dict(base.weights),
                params=legacy_params,
                active=False,
            )
        )
        session.add(
            InjectionEvent(
                event_uid="01KZ4R20000000000000000000",
                injection_id=injection_id,
                thread_id=UUID(int=9303),
                agent_id="general",
                machine_id="studio",
                principal_id="owner",
                project_key=None,
                agent_kind="general",
                prompt_text="audition fixture",
                scorer_version=ACTIVE_SCORER_VERSION,
                memory_id=memory_id,
                memory_kind="fact",
                features={
                    "sem": 0.5,
                    "kw": 0.25,
                    "time": 1.0,
                    "proj": 0.5,
                    "freq": 0.0,
                    "hist": 0.5,
                    "_memory": {"label": "Audition memory", "body": "Audition body"},
                    "_prepare": {"model_context_tokens": 8192},
                },
                score=0.4,
                rank=1,
                shown_as="injected",
                actor_class="human",
                outcome=None,
                ts=datetime(2026, 8, 3, 15, tzinfo=UTC),
            )
        )

    before = await memory_client.post(
        "/v1/scorer-console/query",
        params={"scope": "palace"},
        json={"principal_id": "local", "thread_id": None, "as_of": "now"},
    )
    assert before.status_code == 200
    accuracy = {item["version"]: item for item in before.json()["accuracy"]}
    assert accuracy["learner-proposal"] | {"created_at": None} == {
        "version": "learner-proposal",
        "created_at": None,
        "status": "measured",
        "accuracy_percent": "90.90909090909090909090909091",
        "holdout_dispositions": 10,
        "disagreements": 1,
        "weighted_dispositions": "5.5",
        "weighted_wrong": "0.5",
    }
    assert accuracy["legacy-proposal"]["status"] == "not_recorded"
    assert accuracy["legacy-proposal"]["accuracy_percent"] is None

    audition = await memory_client.post(
        "/v1/scorer-auditions",
        json={
            "principal_id": "owner",
            "injection_id": str(injection_id),
            "proposal_version": "learner-proposal",
        },
    )
    assert audition.status_code == 200
    assert audition.json()["instant"]["status"] == "ready"
    async with memory_session_factory() as session:
        incumbent = (
            (await session.execute(select(ScorerConfigRow).where(ScorerConfigRow.active.is_(True))))
            .scalars()
            .one()
        )
    assert incumbent.version == ACTIVE_SCORER_VERSION

    activated = await memory_client.post(
        "/v1/scorer-configs/learner-proposal/activate",
        json={
            "event_uid": "01KZ4R20000000000000000001",
            "actor_class": "human",
            "machine_id": "studio",
        },
    )
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"
