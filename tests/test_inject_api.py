"""S3 live-Postgres contract tests for injection preparation."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from conftest import ACTIVE_SCORER_VERSION, ScriptedEmbeddingProvider, basis_vector
from httpx import AsyncClient, Response
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import spine.inject.service as inject_service
from spine.db.memory import CasUpdate, MemoryUnitChanges, cas_update_memory_unit
from spine.db.models import InjectionEvent, MemoryRevision, MemoryUnit, Thread
from spine.embeddings import EmbeddingTransportError
from spine.ids import mint_ulid

FEATURE_NAMES = {
    "sem", "kw", "time", "proj", "freq", "hist", "loc", "thread", "where",
}
DEFAULT_STATS = {
    "injections": 0,
    "removals": 0,
    "citations": 0,
    "never_kills": 0,
    "last_injected_at": None,
}
ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class _RecordingLearnerWorker:
    def __init__(self) -> None:
        self.notifications = 0
        self.triggers = []

    def notify(self, trigger: object = None) -> None:
        self.notifications += 1
        self.triggers.append(trigger)


def _ulid(seed: int) -> str:
    """Encode a small deterministic integer as a canonical ULID."""

    characters = ["0"] * 26
    for index in range(25, -1, -1):
        characters[index] = ULID_ALPHABET[seed & 31]
        seed >>= 5
    return "".join(characters)


def _prepare_body(
    *,
    thread_id: UUID | None = None,
    prompt: str = "The Alpha launch plan",
    model_context_tokens: int = 100_000,
    **overrides: Any,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "thread_id": str(thread_id or uuid4()),
        "agent_id": "agent-1",
        "machine_id": "machine-1",
        "principal_id": "owner",
        "project_key": "alpha",
        "prompt": prompt,
        "model_context_tokens": model_context_tokens,
    }
    request.update(overrides)
    return request


def _assert_json(response: Response, status: int) -> dict[str, Any]:
    assert response.status_code == status
    assert response.headers["content-type"].split(";", 1)[0] == "application/json"
    return response.json()


def _assert_problem(response: Response, status: int) -> dict[str, Any]:
    assert response.status_code == status
    assert response.headers["content-type"].split(";", 1)[0] == "application/problem+json"
    problem = response.json()
    assert problem["type"] == "about:blank"
    assert problem["status"] == status
    assert problem["title"]
    assert problem["detail"]
    assert problem["endpoint"] == "POST /v1/inject/prepare"
    return problem


async def _insert_memory(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    memory_id: UUID,
    label: str,
    body: str,
    embedding: list[float],
    principal_id: str = "owner",
    kind: str = "fact",
    keywords: list[str] | None = None,
    project_key: str | None = "alpha",
    origin_path: str | None = None,
    pin: bool = False,
    status: str = "active",
    stats: dict[str, Any] | None = None,
    bias: float = 0.0,
    updated_at: datetime | None = None,
    revision_editor: str = "fixture",
    revision_ts: datetime | None = None,
) -> str:
    """Insert a complete root head/revision with controlled scorer state."""

    timestamp = updated_at or datetime.now(UTC)
    root_uid = _ulid(memory_id.int)
    async with session_factory() as session:
        async with session.begin():
            session.add(
                MemoryUnit(
                    id=memory_id,
                    principal_id=principal_id,
                    label=label,
                    body=body,
                    kind=kind,
                    keywords=keywords or [],
                    embedding=embedding,
                    embedding_model="test-embedding-1536",
                    project_key=project_key,
                    thread_origin=None,
                    origin_path=origin_path,
                    pin=pin,
                    status=status,
                    revision=1,
                    stats=dict(stats or DEFAULT_STATS),
                    bias=bias,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            # The mappings intentionally expose no ORM relationship, so make the
            # FK ordering explicit instead of relying on unit-of-work table order.
            await session.flush()
            session.add(
                MemoryRevision(
                    rev_uid=root_uid,
                    parent_uid=None,
                    memory_id=memory_id,
                    revision=1,
                    body=body,
                    label=label,
                    editor=revision_editor,
                    origin_machine_id="fixture-machine",
                    reason="fixture root",
                    ts=revision_ts or timestamp,
                )
            )
    return root_uid


async def test_prepare_commit_replays_gate_and_prepare_updates_only_injected(
    memory_app,
    memory_client: AsyncClient,
    embedding_provider: ScriptedEmbeddingProvider,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-030/A-051 defend replay plus success-only prepare/commit learner wakes."""
    learner_worker = _RecordingLearnerWorker()
    memory_app.state.learner_worker = learner_worker
    now = datetime.now(UTC)
    injected_id = UUID(int=101)
    near_id = UUID(int=102)
    filtered_ids = [UUID(int=value) for value in (103, 104, 105)]
    injected_updated_at = now - timedelta(days=14)
    human_edit_at = now - timedelta(days=7)
    root_uid = await _insert_memory(
        memory_session_factory,
        memory_id=injected_id,
        label="Plan",
        body="Ship the alpha launch safely.",
        embedding=basis_vector(0),
        kind="project_note",
        keywords=["ALPHA-launch"],
        stats=DEFAULT_STATS | {"citations": 5},
        updated_at=injected_updated_at,
        revision_editor="user",
        revision_ts=human_edit_at,
    )
    await _insert_memory(
        memory_session_factory,
        memory_id=near_id,
        label="Unrelated global",
        body="A deliberately weak global candidate.",
        embedding=basis_vector(1),
        project_key=None,
        updated_at=now - timedelta(days=365),
    )
    await _insert_memory(
        memory_session_factory,
        memory_id=filtered_ids[0],
        label="Wrong project",
        body="Must not cross projects.",
        embedding=basis_vector(0),
        keywords=["alpha"],
        project_key="beta",
    )
    await _insert_memory(
        memory_session_factory,
        memory_id=filtered_ids[1],
        label="Wrong owner",
        body="Must not cross principals.",
        embedding=basis_vector(0),
        keywords=["alpha"],
        principal_id="someone-else",
    )
    await _insert_memory(
        memory_session_factory,
        memory_id=filtered_ids[2],
        label="Quarantined",
        body="Inactive candidates stay out.",
        embedding=basis_vector(0),
        keywords=["alpha"],
        status="quarantined",
    )
    prompt = "The Alpha launch plan"
    embedding_provider.set(prompt, basis_vector(0))
    request = _prepare_body(prompt=prompt)

    payload = _assert_json(await memory_client.post("/v1/inject/prepare", json=request), 200)
    assert learner_worker.notifications == 0  # D.2 153: compaction owns the trigger.

    assert set(payload) == {
        "injection_id",
        "snapshot_ts",
        "scorer_version",
        "injected",
        "near_misses",
        "final_block",
        "memory_allocation",
    }
    assert payload["final_block"] is None
    UUID(payload["injection_id"])
    snapshot_ts = datetime.fromisoformat(payload["snapshot_ts"])
    assert payload["scorer_version"] == ACTIVE_SCORER_VERSION
    assert payload["memory_allocation"]["memory_context_share"] == 0.10
    assert payload["memory_allocation"]["regular_tokens"] > 0
    assert payload["memory_allocation"]["pinned_tokens"] == 0
    assert [card["memory_id"] for card in payload["injected"]] == [str(injected_id)]
    assert [card["memory_id"] for card in payload["near_misses"]] == [str(near_id)]

    injected = payload["injected"][0]
    assert set(injected) == {
        "memory_id",
        "label",
        "body",
        "kind",
        "pin",
        "score",
        "features",
        "rank",
    }
    assert injected | {"score": None, "features": None} == {
        "memory_id": str(injected_id),
        "label": "Plan",
        "body": "Ship the alpha launch safely.",
        "kind": "project_note",
        "pin": False,
        "score": None,
        "features": None,
        "rank": 1,
    }
    age_days = max(0.0, (snapshot_ts - injected_updated_at).total_seconds() / 86_400)
    edit_age_days = max(0.0, (snapshot_ts - human_edit_at).total_seconds() / 86_400)
    expected_features = {
        "sem": 1.0,
        "kw": 1.0,
        "time": 2 ** (-age_days / 14),
        "proj": 1.0,
        "freq": 0.5,
        "hist": 2 ** (-edit_age_days / 7),
    }
    assert set(injected["features"]) == FEATURE_NAMES
    assert injected["features"]["loc"] is None
    assert injected["features"]["thread"] is None
    assert injected["features"]["where"] is None
    assert {key: injected["features"][key] for key in expected_features} == pytest.approx(
        expected_features, abs=2e-5
    )
    expected_score = (
        0.42 * expected_features["sem"]
        + 0.16 * expected_features["kw"]
        + 0.11 * expected_features["time"]
        + 0.16 * expected_features["proj"]
        + 0.08 * expected_features["freq"]
        + 0.07 * expected_features["hist"]
    )
    assert injected["score"] == pytest.approx(expected_score, abs=2e-5)

    near = payload["near_misses"][0]
    assert near["rank"] == 2
    assert near["features"]["sem"] == pytest.approx(0.0, abs=1e-6)
    assert near["features"]["proj"] == pytest.approx(0.5)
    assert near["features"]["loc"] is None
    assert near["features"]["thread"] is None
    assert near["features"]["where"] is None
    assert set(near["features"]) == FEATURE_NAMES
    assert embedding_provider.calls == [(prompt,)]

    async with memory_session_factory() as session:
        events = (
            await session.scalars(
                select(InjectionEvent)
                .where(InjectionEvent.injection_id == UUID(payload["injection_id"]))
                .order_by(InjectionEvent.rank)
            )
        ).all()
        injected_head = await session.get(MemoryUnit, injected_id)
        near_head = await session.get(MemoryUnit, near_id)
        revisions = (
            await session.scalars(
                select(MemoryRevision)
                .where(MemoryRevision.memory_id.in_([injected_id, near_id]))
                .order_by(MemoryRevision.memory_id, MemoryRevision.revision)
            )
        ).all()
        thread = await session.get(Thread, UUID(request["thread_id"]))

    assert [(event.memory_id, event.shown_as, event.outcome) for event in events] == [
        (injected_id, "injected", None),
        (near_id, "near_miss", None),
    ]
    expected_sources = (["vector", "fts"], ["vector"])
    for event, card, sources in zip(
        events,
        [injected, near],
        expected_sources,
        strict=True,
    ):
        assert event.score == card["score"]
        assert event.rank == card["rank"]
        assert {name: event.features[name] for name in FEATURE_NAMES} == card["features"]
        assert event.features["_retrieval"] == {"sources": sources}
        assert event.features["_memory"] == {
            "label": card["label"],
            "body": card["body"],
            "pin": card["pin"],
            "updated_at": event.features["_memory"]["updated_at"],
        }
    assert datetime.fromisoformat(events[0].features["_memory"]["updated_at"]) == (
        injected_updated_at
    )
    assert events[0].agent_kind == "general"
    assert events[0].prompt_text == prompt

    assert thread is not None
    assert thread.snapshot_ts == snapshot_ts
    assert (thread.principal_id, thread.agent_id, thread.machine_id, thread.project_key) == (
        "owner",
        "agent-1",
        "machine-1",
        "alpha",
    )
    assert injected_head is not None and near_head is not None
    assert injected_head.revision == 2
    assert injected_head.stats["injections"] == 1
    assert datetime.fromisoformat(injected_head.stats["last_injected_at"]) == snapshot_ts
    assert near_head.revision == 1
    assert near_head.stats == DEFAULT_STATS
    assert [(revision.memory_id, revision.revision) for revision in revisions] == [
        (injected_id, 1),
        (injected_id, 2),
        (near_id, 1),
    ]
    system_revision = revisions[1]
    assert system_revision.parent_uid == root_uid
    assert (system_revision.editor, system_revision.origin_machine_id, system_revision.reason) == (
        "system:inject",
        "machine-1",
        "inject/prepare",
    )

    commit = _assert_json(
        await memory_client.post(
            "/v1/inject/commit",
            json={
                "injection_id": payload["injection_id"],
                "removed": [
                    {
                        "memory_id": injected["memory_id"],
                        "reason": "not_relevant",
                    }
                ],
                "added_back": [near["memory_id"]],
            },
        ),
        200,
    )
    assert near["body"] in commit["final_block"]
    assert injected["body"] not in commit["final_block"]
    assert learner_worker.notifications == 0

    missing = await memory_client.post(
        "/v1/inject/commit",
        json={
            "injection_id": payload["injection_id"],
            "removed": [{"memory_id": str(uuid4()), "reason": "wrong"}],
            "added_back": [],
        },
    )
    assert missing.status_code == 422
    assert learner_worker.notifications == 0

    async with memory_session_factory() as session:
        replayed = (
            await session.scalars(
                select(InjectionEvent)
                .where(InjectionEvent.injection_id == UUID(payload["injection_id"]))
                .order_by(InjectionEvent.rank)
            )
        ).all()

    assert [
        {
            "memory_id": str(event.memory_id),
            "shown_as": event.shown_as,
            "score": event.score,
            "features": {name: event.features[name] for name in FEATURE_NAMES},
            "prompt_text": event.prompt_text,
            "scorer_version": event.scorer_version,
            "outcome": event.outcome,
            "memory": event.features["_memory"],
        }
        for event in replayed
    ] == [
        {
            "memory_id": card["memory_id"],
            "shown_as": shown_as,
            "score": card["score"],
            "features": card["features"],
            "prompt_text": prompt,
            "scorer_version": payload["scorer_version"],
            "outcome": outcome,
            "memory": {
                "label": card["label"],
                "body": card["body"],
                "pin": card["pin"],
                "updated_at": replayed[index].features["_memory"]["updated_at"],
            },
        }
        for index, (card, shown_as, outcome) in enumerate(
            [
                (injected, "injected", "removed:not_relevant"),
                (near, "near_miss", "added_back"),
            ]
        )
    ]


async def test_exact_keyword_with_weak_embedding_reaches_the_gate_via_fts(
    memory_client: AsyncClient,
    embedding_provider: ScriptedEmbeddingProvider,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-030 is defended by verifying that exact keyword with weak embedding reaches the gate
    via fts; this prevents drift in the per-message injection transaction contract.
    """
    now = datetime.now(UTC)
    stale = now - timedelta(days=3650)
    target_id = UUID(int=999)
    for value in range(1000, 1050):
        await _insert_memory(
            memory_session_factory,
            memory_id=UUID(int=value),
            label=f"Semantic decoy {value}",
            body="A stale global memory with no lexical match.",
            embedding=basis_vector(0),
            project_key=None,
            updated_at=stale,
        )
    await _insert_memory(
        memory_session_factory,
        memory_id=target_id,
        label="Launch cipher",
        body="Remember the exact project identifier.",
        embedding=basis_vector(1),
        keywords=["umbrahelix"],
        stats=DEFAULT_STATS | {"citations": 10},
        updated_at=now,
        revision_editor="user",
        revision_ts=now,
    )

    prompt = "umbrahelix"
    embedding_provider.set(prompt, basis_vector(0))
    response = _assert_json(
        await memory_client.post(
            "/v1/inject/prepare",
            json=_prepare_body(prompt=prompt),
        ),
        200,
    )

    assert [UUID(card["memory_id"]) for card in response["injected"]] == [target_id]
    card = response["injected"][0]
    assert set(card["features"]) == FEATURE_NAMES
    assert card["features"]["sem"] == pytest.approx(0.0)
    assert card["features"]["kw"] == pytest.approx(1.0)
    assert card["score"] == pytest.approx(0.58, abs=2e-5)

    async with memory_session_factory() as session:
        event = await session.scalar(
            select(InjectionEvent).where(
                InjectionEvent.injection_id == UUID(response["injection_id"]),
                InjectionEvent.memory_id == target_id,
            )
        )

    assert event is not None
    assert event.shown_as == "injected"
    assert event.features["_retrieval"] == {"sources": ["fts"]}


async def test_r16_prepare_ranks_same_location_above_equal_two_hop_memory(
    memory_client: AsyncClient,
    embedding_provider: ScriptedEmbeddingProvider,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-058 makes R16 live through C.4 and the migrated scorer, not only pure math."""

    same_id = UUID(int=1101)
    far_id = UUID(int=1102)
    for memory_id, label, origin_path in (
        (same_id, "Same feet", "src/feature"),
        (far_id, "Other branch", "src/other"),
    ):
        await _insert_memory(
            memory_session_factory,
            memory_id=memory_id,
            label=label,
            body="Equal semantic evidence.",
            embedding=basis_vector(0),
            origin_path=origin_path,
        )
    prompt = "location proof"
    embedding_provider.set(prompt, basis_vector(0))

    payload = _assert_json(
        await memory_client.post(
            "/v1/inject/prepare",
            json=_prepare_body(prompt=prompt, location_path="src/feature"),
        ),
        200,
    )

    assert payload["scorer_version"] == ACTIVE_SCORER_VERSION
    cards = {UUID(card["memory_id"]): card for card in payload["injected"]}
    assert cards[same_id]["features"]["loc"] == pytest.approx(1.0)
    assert cards[far_id]["features"]["loc"] == pytest.approx(0.5)
    assert cards[same_id]["score"] > cards[far_id]["score"]
    assert cards[same_id]["rank"] < cards[far_id]["rank"]


async def test_hybrid_pool_uses_or_terms_and_exact_uuid_tie_boundaries(
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-030 is defended by verifying that hybrid pool uses or terms and exact uuid tie
    boundaries; this prevents drift in the per-message injection transaction contract.
    """
    memory_ids = [UUID(int=value) for value in range(2000, 2051)]
    for memory_id in reversed(memory_ids):
        await _insert_memory(
            memory_session_factory,
            memory_id=memory_id,
            label=f"Equal lexical rank {memory_id.int}",
            body="Same document shape for every candidate.",
            embedding=basis_vector(1),
            keywords=["tielexeme"],
        )

    command = inject_service.PrepareCommand(
        thread_id=uuid4(),
        agent_id="agent-1",
        machine_id="machine-1",
        principal_id="owner",
        project_key="alpha",
        agent_kind="general",
        prompt="tielexeme absentterm",
        model_context_tokens=100_000,
    )
    async with memory_session_factory() as session:
        # Avoid the small-table sequential-scan happy path. All 50 returned
        # rows must still carry both legs, proving the exact vector boundary
        # is not capped by HNSW's default breadth of 40.
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        pinned, regular = await inject_service._load_candidates(
            session,
            command=command,
            query_embedding=basis_vector(0),
            candidate_pool=50,
        )

    assert pinned == []
    assert [candidate.memory_id for candidate in regular] == memory_ids[:50]
    assert all(candidate.pool_sources == ("vector", "fts") for candidate in regular)


async def test_pins_bypass_threshold_and_budget_and_regular_ties_use_memory_id(
    memory_client: AsyncClient,
    embedding_provider: ScriptedEmbeddingProvider,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-030 is defended by verifying that pins bypass threshold and budget and regular ties
    use memory id; this prevents drift in the per-message injection transaction contract.
    """
    pin_ids = [UUID(int=201), UUID(int=202)]
    regular_ids = [UUID(int=value) for value in (203, 204, 205, 206)]
    same_timestamp = datetime.now(UTC)
    # Insert in reverse order so storage order cannot accidentally satisfy the contract.
    for index, memory_id in reversed(list(enumerate(pin_ids))):
        await _insert_memory(
            memory_session_factory,
            memory_id=memory_id,
            label=f"Pinned {index}",
            body="pinned content consumes several tokens",
            embedding=basis_vector(1),
            pin=True,
            updated_at=same_timestamp,
        )
    for index, memory_id in reversed(list(enumerate(regular_ids))):
        await _insert_memory(
            memory_session_factory,
            memory_id=memory_id,
            label=f"Regular {index}",
            body="x",
            embedding=basis_vector(0),
            kind="pinned" if index == 0 else "fact",
            updated_at=same_timestamp,
        )

    prompt = "needle"
    embedding_provider.set(prompt, basis_vector(0))
    response = _assert_json(
        await memory_client.post(
            "/v1/inject/prepare",
            json=_prepare_body(prompt=prompt, model_context_tokens=20),
        ),
        200,
    )

    assert [UUID(card["memory_id"]) for card in response["injected"]] == [
        *pin_ids,
        *regular_ids[:2],
    ]
    assert [card["rank"] for card in response["injected"]] == [1, 2, 3, 4]
    assert [card["pin"] for card in response["injected"]] == [True, True, False, False]
    assert [UUID(card["memory_id"]) for card in response["near_misses"]] == regular_ids[2:]
    assert [card["rank"] for card in response["near_misses"]] == [5, 6]
    assert response["memory_allocation"]["share_tokens"] == 2
    assert response["memory_allocation"]["regular_tokens"] == 2
    assert response["memory_allocation"]["pinned_tokens"] > 2
    assert response["memory_allocation"]["pinned_overflow_tokens"] > 0
    assert [card["score"] for card in response["near_misses"]] == pytest.approx(
        [response["near_misses"][0]["score"]] * len(response["near_misses"]), abs=2e-5
    )

    async with memory_session_factory() as session:
        events = (
            await session.scalars(
                select(InjectionEvent)
                .where(InjectionEvent.injection_id == UUID(response["injection_id"]))
                .order_by(InjectionEvent.rank)
            )
        ).all()
        heads = {
            unit.id: unit
            for unit in (
                await session.scalars(
                    select(MemoryUnit).where(MemoryUnit.id.in_(pin_ids + regular_ids))
                )
            ).all()
        }

    presented = [event for event in events if event.shown_as != "budget_cut"]
    budget_cuts = [event for event in events if event.shown_as == "budget_cut"]
    assert [event.shown_as for event in presented] == [
        "pinned",
        "pinned",
        "injected",
        "injected",
        "near_miss",
        "near_miss",
    ]
    assert [event.features["_retrieval"] for event in presented] == [
        {"sources": ["pinned"]},
        {"sources": ["pinned"]},
        {"sources": ["vector"]},
        {"sources": ["vector"]},
        {"sources": ["vector"]},
        {"sources": ["vector"]},
    ]
    assert [event.memory_id for event in budget_cuts] == regular_ids[2:]
    assert all(event.outcome == "budget_cut" for event in budget_cuts)
    assert all(heads[memory_id].revision == 2 for memory_id in pin_ids)
    assert all(heads[memory_id].stats["injections"] == 1 for memory_id in pin_ids)
    assert all(heads[memory_id].revision == 2 for memory_id in regular_ids[:2])
    assert all(heads[memory_id].stats["injections"] == 1 for memory_id in regular_ids[:2])
    assert all(heads[memory_id].revision == 1 for memory_id in regular_ids[2:])
    assert all(heads[memory_id].stats == DEFAULT_STATS for memory_id in regular_ids[2:])


async def test_budget_skip_continues_to_lower_scoring_candidate(
    memory_client: AsyncClient,
    embedding_provider: ScriptedEmbeddingProvider,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """D.2 101(3) and SPEC B.6 rule 12 require budget skips to remain behaviorally inert but
    replay-visible; this prevents the memory-share learner from losing its lower boundary.
    """
    high_id = UUID(int=301)
    lower_id = UUID(int=302)
    timestamp = datetime.now(UTC)
    await _insert_memory(
        memory_session_factory,
        memory_id=high_id,
        label="Higher but too large",
        body="one two three",
        embedding=basis_vector(0),
        bias=0.1,
        updated_at=timestamp,
    )
    await _insert_memory(
        memory_session_factory,
        memory_id=lower_id,
        label="Lower and small",
        body="x",
        embedding=basis_vector(0),
        updated_at=timestamp,
    )
    prompt = "needle"
    embedding_provider.set(prompt, basis_vector(0))

    response = _assert_json(
        await memory_client.post(
            "/v1/inject/prepare",
            json=_prepare_body(prompt=prompt, model_context_tokens=20),
        ),
        200,
    )

    assert [UUID(card["memory_id"]) for card in response["injected"]] == [lower_id]
    assert response["injected"][0]["rank"] == 2
    assert [UUID(card["memory_id"]) for card in response["near_misses"]] == [high_id]
    assert response["near_misses"][0]["rank"] == 1
    assert response["near_misses"][0]["score"] > response["injected"][0]["score"]

    async with memory_session_factory() as session:
        high = await session.get(MemoryUnit, high_id)
        lower = await session.get(MemoryUnit, lower_id)
        events = list(
            (await session.execute(select(InjectionEvent).order_by(InjectionEvent.rank))).scalars()
        )
    assert high is not None and lower is not None
    assert (high.revision, high.stats["injections"]) == (1, 0)
    assert (lower.revision, lower.stats["injections"]) == (2, 1)
    assert [(event.memory_id, event.shown_as, event.outcome) for event in events] == [
        (high_id, "near_miss", None),
        (high_id, "budget_cut", "budget_cut"),
        (lower_id, "injected", None),
    ]
    assert set(events[1].features) >= {"sem", "kw", "time", "proj", "freq", "hist"}
    assert events[1].scorer_version


async def test_concurrent_prepare_is_one_shot_per_thread(
    memory_client: AsyncClient,
    embedding_provider: ScriptedEmbeddingProvider,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-030 is defended by verifying that concurrent prepare is one shot per thread; this
    prevents drift in the per-message injection transaction contract.
    """
    prompt = "prepare exactly once"
    embedding_provider.set(prompt, basis_vector(0))
    thread_id = uuid4()
    request = _prepare_body(thread_id=thread_id, prompt=prompt)

    responses = await asyncio.gather(
        memory_client.post("/v1/inject/prepare", json=request),
        memory_client.post("/v1/inject/prepare", json=request),
    )

    assert sorted(response.status_code for response in responses) == [200, 409]
    success = next(response for response in responses if response.status_code == 200)
    conflict = next(response for response in responses if response.status_code == 409)
    assert _assert_json(success, 200)["injected"] == []
    _assert_problem(conflict, 409)
    async with memory_session_factory() as session:
        thread = await session.get(Thread, thread_id)
        event_count = await session.scalar(select(func.count()).select_from(InjectionEvent))
    assert thread is not None and thread.snapshot_ts is not None
    assert event_count == 0

    repeated = await memory_client.post("/v1/inject/prepare", json=request)
    _assert_problem(repeated, 409)


async def test_autonomous_prepare_preserves_locks_and_logs_entry_keep_exit(
    memory_app,
    memory_client: AsyncClient,
    embedding_provider: ScriptedEmbeddingProvider,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-030/A-051 defend autonomous evidence and success-only prepare/feedback wakes."""
    learner_worker = _RecordingLearnerWorker()
    memory_app.state.learner_worker = learner_worker
    confirmed_id = UUID(int=310)
    autonomous_id = UUID(int=311)
    entered_id = UUID(int=312)
    for memory_id, label, embedding in (
        (confirmed_id, "Human confirmed", basis_vector(1)),
        (autonomous_id, "Old autonomous", basis_vector(1)),
        (entered_id, "New autonomous", basis_vector(0)),
    ):
        await _insert_memory(
            memory_session_factory,
            memory_id=memory_id,
            label=label,
            body=f"{label} body",
            embedding=embedding,
        )

    thread_id = uuid4()
    first_prompt = "first topic"
    second_prompt = "second topic"
    embedding_provider.set(first_prompt, basis_vector(1))
    embedding_provider.set(second_prompt, basis_vector(0))
    first = _assert_json(
        await memory_client.post(
            "/v1/inject/prepare",
            json=_prepare_body(thread_id=thread_id, prompt=first_prompt),
        ),
        200,
    )
    first_ids = [UUID(card["memory_id"]) for card in first["injected"]]
    assert first_ids == [confirmed_id, autonomous_id]
    assert learner_worker.notifications == 0

    second = _assert_json(
        await memory_client.post(
            "/v1/inject/prepare",
            json=_prepare_body(
                thread_id=thread_id,
                prompt=second_prompt,
                mode="autonomous",
                current_memory_ids=[str(confirmed_id), str(autonomous_id)],
                confirmed_memory_ids=[str(confirmed_id)],
                excluded_memory_ids=[],
            ),
        ),
        200,
    )

    assert [UUID(card["memory_id"]) for card in second["injected"]] == [
        confirmed_id,
        entered_id,
    ]
    assert second["final_block"] is not None
    assert "Human confirmed body" in second["final_block"]
    assert "New autonomous body" in second["final_block"]
    assert "Old autonomous body" not in second["final_block"]
    assert datetime.fromisoformat(second["snapshot_ts"]) > datetime.fromisoformat(
        first["snapshot_ts"]
    )

    async with memory_session_factory() as session:
        rows = (
            await session.scalars(
                select(InjectionEvent).where(
                    InjectionEvent.injection_id == UUID(second["injection_id"])
                )
            )
        ).all()
    outcomes = {row.memory_id: row.outcome for row in rows}
    assert outcomes == {
        confirmed_id: "kept",
        autonomous_id: "auto_exited",
        entered_id: "auto_entered",
    }
    assert {row.actor_class for row in rows} == {"passive"}
    assert learner_worker.notifications == 0

    assert _assert_json(
        await memory_client.post(
            "/v1/feedback",
            json={
                "injection_id": second["injection_id"],
                "memory_id": str(entered_id),
                "signal": "mid_thread_removed",
            },
        ),
        200,
    ) == {"ok": True}
    assert learner_worker.notifications == 0

    missing = await memory_client.post(
        "/v1/feedback",
        json={
            "injection_id": str(uuid4()),
            "memory_id": str(uuid4()),
            "signal": "cited",
        },
    )
    assert missing.status_code == 404
    assert learner_worker.notifications == 0
    async with memory_session_factory() as session:
        entered_event = (
            await session.scalars(
                select(InjectionEvent).where(
                    InjectionEvent.injection_id == UUID(second["injection_id"]),
                    InjectionEvent.memory_id == entered_id,
                )
            )
        ).one()
    assert entered_event.outcome == "mid_thread_removed"


async def test_unstamped_thread_requires_exact_identity_before_stamping(
    memory_client: AsyncClient,
    embedding_provider: ScriptedEmbeddingProvider,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-030 is defended by verifying that unstamped thread requires exact identity before
    stamping; this prevents drift in the per-message injection transaction contract.
    """
    thread_id = uuid4()
    prompt = "existing thread"
    embedding_provider.set(prompt, basis_vector(0))
    async with memory_session_factory() as session:
        async with session.begin():
            session.add(
                Thread(
                    id=thread_id,
                    principal_id="owner",
                    agent_id="agent-1",
                    machine_id="machine-1",
                    project_key="alpha",
                    snapshot_ts=None,
                )
            )

    mismatched = await memory_client.post(
        "/v1/inject/prepare",
        json=_prepare_body(thread_id=thread_id, prompt=prompt, machine_id="machine-2"),
    )
    _assert_problem(mismatched, 409)
    async with memory_session_factory() as session:
        unchanged = await session.get(Thread, thread_id)
    assert unchanged is not None and unchanged.snapshot_ts is None

    accepted = _assert_json(
        await memory_client.post(
            "/v1/inject/prepare",
            json=_prepare_body(thread_id=thread_id, prompt=prompt),
        ),
        200,
    )
    async with memory_session_factory() as session:
        stamped = await session.get(Thread, thread_id)
    assert stamped is not None
    assert stamped.snapshot_ts == datetime.fromisoformat(accepted["snapshot_ts"])


async def test_concurrent_threads_do_not_lose_injection_cas_updates(
    memory_client: AsyncClient,
    embedding_provider: ScriptedEmbeddingProvider,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-030 is defended by verifying that concurrent threads do not lose injection cas
    updates; this prevents drift in the per-message injection transaction contract.
    """
    memory_id = UUID(int=350)
    await _insert_memory(
        memory_session_factory,
        memory_id=memory_id,
        label="Shared candidate",
        body="selected by both threads",
        embedding=basis_vector(0),
    )
    prompt = "shared candidate"
    embedding_provider.set(prompt, basis_vector(0))

    responses = await asyncio.gather(
        memory_client.post(
            "/v1/inject/prepare",
            json=_prepare_body(thread_id=uuid4(), prompt=prompt),
        ),
        memory_client.post(
            "/v1/inject/prepare",
            json=_prepare_body(thread_id=uuid4(), prompt=prompt),
        ),
    )

    payloads = [_assert_json(response, 200) for response in responses]
    assert all(
        [UUID(card["memory_id"]) for card in payload["injected"]] == [memory_id]
        for payload in payloads
    )
    assert len({payload["injection_id"] for payload in payloads}) == 2
    async with memory_session_factory() as session:
        head = await session.get(MemoryUnit, memory_id)
        revisions = await session.scalar(
            select(func.count())
            .select_from(MemoryRevision)
            .where(MemoryRevision.memory_id == memory_id)
        )
        events = await session.scalar(
            select(func.count())
            .select_from(InjectionEvent)
            .where(InjectionEvent.memory_id == memory_id)
        )
    assert head is not None
    assert (head.revision, head.stats["injections"], revisions, events) == (3, 2, 3, 2)


async def test_prepare_reads_one_frozen_database_snapshot(
    memory_client: AsyncClient,
    embedding_provider: ScriptedEmbeddingProvider,
    memory_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A-030 is defended by verifying that prepare reads one frozen database snapshot; this
    prevents drift in the per-message injection transaction contract.
    """
    frozen_id = UUID(int=401)
    post_snapshot_id = UUID(int=402)
    await _insert_memory(
        memory_session_factory,
        memory_id=frozen_id,
        label="Frozen candidate",
        body="body visible at the snapshot",
        embedding=basis_vector(1),
    )
    prompt = "snapshot boundary"
    embedding_provider.set(prompt, basis_vector(0))

    snapshot_stamped = asyncio.Event()
    continue_prepare = asyncio.Event()
    original_stamp_thread = inject_service._stamp_thread

    async def pause_after_stamp(
        session: AsyncSession,
        command: inject_service.PrepareCommand,
        snapshot_ts: datetime,
    ) -> None:
        await original_stamp_thread(session, command, snapshot_ts)
        snapshot_stamped.set()
        await continue_prepare.wait()

    monkeypatch.setattr(inject_service, "_stamp_thread", pause_after_stamp)
    request = _prepare_body(prompt=prompt)
    pending = asyncio.create_task(memory_client.post("/v1/inject/prepare", json=request))
    try:
        await asyncio.wait_for(snapshot_stamped.wait(), timeout=2)
        async with memory_session_factory() as session:
            async with session.begin():
                await cas_update_memory_unit(
                    session,
                    CasUpdate(
                        memory_id=frozen_id,
                        expected_revision=1,
                        rev_uid=mint_ulid(),
                        editor="user",
                        origin_machine_id="machine-2",
                        reason="after snapshot",
                        changes=MemoryUnitChanges(
                            body="body written after the snapshot",
                            embedding=basis_vector(0),
                            embedding_model="test-embedding-1536",
                        ),
                    ),
                )
        await _insert_memory(
            memory_session_factory,
            memory_id=post_snapshot_id,
            label="Created later",
            body="must not enter the frozen pool",
            embedding=basis_vector(0),
        )
    finally:
        continue_prepare.set()

    payload = _assert_json(await asyncio.wait_for(pending, timeout=2), 200)

    assert payload["injected"] == []
    assert [UUID(card["memory_id"]) for card in payload["near_misses"]] == [frozen_id]
    frozen = payload["near_misses"][0]
    assert frozen["body"] == "body visible at the snapshot"
    assert frozen["features"]["sem"] == pytest.approx(0.0)
    assert post_snapshot_id not in {
        UUID(card["memory_id"]) for card in (*payload["injected"], *payload["near_misses"])
    }

    async with memory_session_factory() as session:
        current = await session.get(MemoryUnit, frozen_id)
        event = (
            await session.scalars(
                select(InjectionEvent).where(
                    InjectionEvent.injection_id == UUID(payload["injection_id"])
                )
            )
        ).one()
    assert current is not None
    assert current.body == "body written after the snapshot"
    assert current.revision == 2
    assert event.features["_memory"]["body"] == "body visible at the snapshot"


async def test_prepare_provider_failure_and_request_validation_are_write_free(
    memory_app,
    memory_client: AsyncClient,
    embedding_provider: ScriptedEmbeddingProvider,
    memory_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A-030/A-051 keep failed prepares write-free and learner-wake-free."""

    learner_worker = _RecordingLearnerWorker()
    memory_app.state.learner_worker = learner_worker

    async def unavailable(_: object) -> list[list[float]]:
        raise EmbeddingTransportError("offline")

    monkeypatch.setattr(embedding_provider, "embed", unavailable)
    failed_thread_id = uuid4()
    unavailable_response = await memory_client.post(
        "/v1/inject/prepare",
        json=_prepare_body(thread_id=failed_thread_id, prompt="provider down"),
    )
    _assert_problem(unavailable_response, 503)

    invalid = _prepare_body(prompt="never embedded")
    invalid.pop("machine_id")
    invalid["invented"] = True
    invalid_response = await memory_client.post("/v1/inject/prepare", json=invalid)
    validation = _assert_problem(invalid_response, 422)
    assert {error["type"] for error in validation["errors"]} == {
        "extra_forbidden",
        "missing",
    }
    assert learner_worker.notifications == 0

    async with memory_session_factory() as session:
        assert await session.get(Thread, failed_thread_id) is None
        assert await session.scalar(select(func.count()).select_from(Thread)) == 0
        assert await session.scalar(select(func.count()).select_from(InjectionEvent)) == 0
