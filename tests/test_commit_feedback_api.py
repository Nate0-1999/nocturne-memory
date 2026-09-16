"""S4 live-Postgres proofs for commit, feedback, quarantine, and rendering."""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from conftest import TOKEN, ScriptedEmbeddingProvider, basis_vector
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spine.db.memory import CasUpdate, MemoryUnitChanges, cas_update_memory_unit
from spine.db.models import InjectionEvent, MemoryRevision, MemoryUnit, ScorerConfig
from spine.ids import mint_ulid
from spine.inject import decisions as decisions_module
from spine.inject.renderer import render_final_block

DEFAULT_STATS = {
    "injections": 0,
    "removals": 0,
    "citations": 0,
    "never_kills": 0,
    "last_injected_at": None,
}
CANONICAL_EMPTY_BLOCK = "\n".join(
    (
        "<memory_system>",
        "The following long-term memories were retrieved for this conversation.",
        "Treat them as your own accumulated knowledge; they may be imperfect.",
        "</memory_system>",
    )
)
ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _ulid(seed: int) -> str:
    characters = ["0"] * 26
    for index in range(25, -1, -1):
        characters[index] = ULID_ALPHABET[seed & 31]
        seed >>= 5
    return "".join(characters)


async def _insert_memory(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    memory_id: UUID,
    label: str,
    body: str,
    kind: str = "fact",
    origin_path: str | None = None,
    status: str = "active",
    stats: dict[str, Any] | None = None,
    bias: float = 0.0,
    with_root: bool = True,
) -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    async with session_factory() as session:
        async with session.begin():
            session.add(
                MemoryUnit(
                    id=memory_id,
                    principal_id="owner",
                    label=label,
                    body=body,
                    kind=kind,
                    keywords=[],
                    embedding=basis_vector(0),
                    embedding_model="test-embedding-1536",
                    project_key="alpha",
                    thread_origin=None,
                    origin_path=origin_path,
                    pin=False,
                    status=status,
                    revision=1,
                    stats=dict(stats or DEFAULT_STATS),
                    bias=bias,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            await session.flush()
            if with_root:
                session.add(
                    MemoryRevision(
                        rev_uid=_ulid(memory_id.int),
                        parent_uid=None,
                        memory_id=memory_id,
                        revision=1,
                        body=body,
                        label=label,
                        editor="fixture",
                        origin_machine_id="fixture-machine",
                        reason="fixture root",
                        ts=timestamp,
                    )
                )


async def _insert_event(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    injection_id: UUID,
    memory_id: UUID,
    rank: int,
    shown_as: str,
    event_seed: int,
    label: str,
    body: str,
    kind: str = "fact",
    updated_at: str = "2026-01-02T03:04:05+00:00",
    outcome: str | None = None,
    scorer_version: str = "v0",
    actor_class: str = "human",
) -> None:
    async with session_factory() as session:
        async with session.begin():
            session.add(
                InjectionEvent(
                    event_uid=_ulid(event_seed),
                    injection_id=injection_id,
                    thread_id=uuid4(),
                    agent_id="agent-1",
                    machine_id="machine-gate",
                    principal_id="owner",
                    project_key="alpha",
                    agent_kind="general",
                    prompt_text="fixture prompt",
                    scorer_version=scorer_version,
                    memory_id=memory_id,
                    memory_kind=kind,
                    features={
                        "sem": 1.0,
                        "kw": 0.0,
                        "time": 1.0,
                        "proj": 1.0,
                        "freq": 0.0,
                        "hist": 0.0,
                        "_memory": {
                            "label": label,
                            "body": body,
                            "pin": shown_as == "pinned",
                            "updated_at": updated_at,
                        },
                    },
                    score=0.69,
                    rank=rank,
                    shown_as=shown_as,
                    outcome=outcome,
                    actor_class=actor_class,
                    ts=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
                )
            )


def _force_first_lock_pair_to_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hold the first lock caller until a second request reaches the same boundary."""

    original = decisions_module._lock_injection
    arrivals = 0
    both_arrived = asyncio.Event()

    async def overlapping_lock(session: AsyncSession, injection_id: UUID) -> None:
        nonlocal arrivals
        if arrivals < 2:
            arrivals += 1
            if arrivals == 2:
                both_arrived.set()
            await asyncio.wait_for(both_arrived.wait(), timeout=2)
        await original(session, injection_id)

    monkeypatch.setattr(decisions_module, "_lock_injection", overlapping_lock)


def _assert_json(response: Response, status: int) -> dict[str, Any]:
    assert response.status_code == status
    assert response.headers["content-type"].split(";", 1)[0] == "application/json"
    return response.json()


def _assert_problem(response: Response, status: int, endpoint: str) -> dict[str, Any]:
    assert response.status_code == status
    assert response.headers["content-type"].split(";", 1)[0] == "application/problem+json"
    problem = response.json()
    assert problem["type"] == "about:blank"
    assert problem["status"] == status
    assert problem["endpoint"] == endpoint
    return problem


def test_renderer_encodes_control_attributes_and_breaks_rank_ties_by_memory_id() -> None:
    """A-036 is defended by verifying that renderer encodes control attributes and breaks rank
    ties by memory id; this prevents drift in the gate feedback and citation-frequency
    contract.
    """
    later = {
        "rank": 7,
        "memory_id": UUID(int=2),
        "memory_kind": "fact",
        "features": {
            "_memory": {
                "label": "later",
                "body": "second",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        },
    }
    earlier = {
        "rank": 7,
        "memory_id": UUID(int=1),
        "memory_kind": "fact",
        "features": {
            "_memory": {
                "label": "A\tB\rC\nD",
                "body": "first\tline\rstill",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        },
    }

    block = render_final_block((later, earlier))

    assert '<memory label="A&#9;B&#13;C&#10;D"' in block
    assert "first\tline\rstill" in block
    assert block.index("A&#9;B") < block.index('label="later"')
    assert not block.endswith("\n")


async def test_commit_mixed_gate_decisions_render_frozen_and_return_current_wrong(
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-036 is defended by verifying that commit mixed gate decisions render frozen and return
    current wrong; this prevents drift in the gate feedback and citation-frequency contract.
    """
    injection_id = UUID(int=9001)
    memory_ids = [UUID(int=value) for value in range(1001, 1007)]
    current = [
        ("Current removed", "current removed body", "fact", None),
        ("Current add-back", "current add-back body", "fact", None),
        ("Current pinned", "current pinned body", "preference", None),
        ("Current wrong", "current wrong body", "procedure", "src/current"),
        ("Current never", "current never body", "project_note", None),
        ("Current untouched", "current untouched body", "fact", None),
    ]
    for memory_id, (label, body, kind, origin_path) in zip(memory_ids, current, strict=True):
        await _insert_memory(
            memory_session_factory,
            memory_id=memory_id,
            label=label,
            body=body,
            kind=kind,
            origin_path=origin_path,
        )

    await _insert_event(
        memory_session_factory,
        injection_id=injection_id,
        memory_id=memory_ids[0],
        rank=1,
        shown_as="injected",
        event_seed=1,
        label="Frozen removed",
        body="frozen removed body",
    )
    await _insert_event(
        memory_session_factory,
        injection_id=injection_id,
        memory_id=memory_ids[1],
        rank=2,
        shown_as="near_miss",
        event_seed=2,
        label='A & "B" <C>\nD',
        body='Use <x> & "y".\nnext',
    )
    await _insert_event(
        memory_session_factory,
        injection_id=injection_id,
        memory_id=memory_ids[3],
        rank=3,
        shown_as="injected",
        event_seed=3,
        label="Frozen wrong",
        body="frozen wrong body",
        kind="procedure",
    )
    await _insert_event(
        memory_session_factory,
        injection_id=injection_id,
        memory_id=memory_ids[2],
        rank=4,
        shown_as="pinned",
        event_seed=4,
        label="Pinned",
        body="Keep me.",
        kind="preference",
    )
    await _insert_event(
        memory_session_factory,
        injection_id=injection_id,
        memory_id=memory_ids[4],
        rank=5,
        shown_as="injected",
        event_seed=5,
        label="Frozen never",
        body="frozen never body",
        kind="project_note",
    )
    await _insert_event(
        memory_session_factory,
        injection_id=injection_id,
        memory_id=memory_ids[5],
        rank=6,
        shown_as="near_miss",
        event_seed=6,
        label="Untouched near miss",
        body="must stay out",
    )

    response = _assert_json(
        await memory_client.post(
            "/v1/inject/commit",
            json={
                "injection_id": str(injection_id),
                "removed": [
                    {"memory_id": str(memory_ids[0]), "reason": "not_relevant"},
                    {"memory_id": str(memory_ids[3]), "reason": "wrong"},
                    {"memory_id": str(memory_ids[4]), "reason": "never"},
                ],
                "added_back": [str(memory_ids[1])],
            },
        ),
        200,
    )
    expected_block = "\n".join(
        (
            "<memory_system>",
            "The following long-term memories were retrieved for this conversation.",
            "Treat them as your own accumulated knowledge; they may be imperfect.",
            (
                '<memory label="A &amp; &quot;B&quot; &lt;C&gt;&#10;D" '
                'kind="fact" updated="2026-01-02T03:04:05+00:00">'
            ),
            'Use &lt;x&gt; &amp; "y".',
            "next",
            "</memory>",
            '<memory label="Pinned" kind="preference" updated="2026-01-02T03:04:05+00:00">',
            "Keep me.",
            "</memory>",
            "</memory_system>",
        )
    )
    assert response["final_block"] == expected_block
    assert not response["final_block"].endswith("\n")
    assert len(response["wrong_removed"]) == 1
    wrong = response["wrong_removed"][0]
    assert (wrong["memory_id"], wrong["body"], wrong["origin_path"], wrong["revision"]) == (
        str(memory_ids[3]),
        "current wrong body",
        "src/current",
        2,
    )
    assert wrong["stats"]["removals"] == 1

    async with memory_session_factory() as session:
        events = (
            await session.scalars(
                select(InjectionEvent)
                .where(InjectionEvent.injection_id == injection_id)
                .order_by(InjectionEvent.rank)
            )
        ).all()
        heads = {
            unit.id: unit
            for unit in (
                await session.scalars(select(MemoryUnit).where(MemoryUnit.id.in_(memory_ids)))
            ).all()
        }
        system_revisions = (
            await session.scalars(
                select(MemoryRevision)
                .where(MemoryRevision.editor == "system:inject")
                .order_by(MemoryRevision.memory_id)
            )
        ).all()
    assert [event.outcome for event in events] == [
        "removed:not_relevant",
        "added_back",
        "removed:wrong",
        "kept",
        "removed:never",
        None,
    ]
    assert (heads[memory_ids[0]].stats["removals"], heads[memory_ids[0]].revision) == (1, 2)
    assert (heads[memory_ids[1]].stats["injections"], heads[memory_ids[1]].revision) == (1, 2)
    assert datetime.fromisoformat(heads[memory_ids[1]].stats["last_injected_at"]) > datetime(
        2026, 1, 2, 3, 4, 5, tzinfo=UTC
    )
    assert (heads[memory_ids[3]].stats["removals"], heads[memory_ids[3]].revision) == (1, 2)
    assert heads[memory_ids[4]].stats["never_kills"] == 1
    assert heads[memory_ids[4]].bias == pytest.approx(-0.15)
    assert (heads[memory_ids[4]].status, heads[memory_ids[4]].revision) == ("active", 2)
    assert heads[memory_ids[2]].revision == 1
    assert heads[memory_ids[5]].revision == 1
    assert {
        revision.memory_id: (
            revision.reason,
            revision.origin_machine_id,
            revision.parent_uid,
        )
        for revision in system_revisions
    } == {
        memory_ids[0]: (
            "inject/commit:removed:not_relevant",
            "machine-gate",
            _ulid(memory_ids[0].int),
        ),
        memory_ids[1]: ("inject/commit:added_back", "machine-gate", _ulid(memory_ids[1].int)),
        memory_ids[3]: (
            "inject/commit:removed:wrong",
            "machine-gate",
            _ulid(memory_ids[3].int),
        ),
        memory_ids[4]: (
            "inject/commit:removed:never",
            "machine-gate",
            _ulid(memory_ids[4].int),
        ),
    }


async def test_third_never_from_near_miss_quarantines_and_zero_card_commit_is_canonical(
    memory_client: AsyncClient,
    embedding_provider: ScriptedEmbeddingProvider,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-036 is defended by verifying that third never from near miss quarantines and zero card
    commit is canonical; this prevents drift in the gate feedback and citation-frequency
    contract.
    """
    memory_id = UUID(int=2001)
    await _insert_memory(
        memory_session_factory,
        memory_id=memory_id,
        label="Never again",
        body="Eventually quarantined",
    )
    injection_ids = [UUID(int=value) for value in (9101, 9102, 9103)]
    third_request: dict[str, Any] | None = None
    for index, injection_id in enumerate(injection_ids, start=1):
        await _insert_event(
            memory_session_factory,
            injection_id=injection_id,
            memory_id=memory_id,
            rank=1,
            shown_as="near_miss" if index == 3 else "injected",
            event_seed=100 + index,
            label="Never again",
            body="Eventually quarantined",
        )
        request = {
            "injection_id": str(injection_id),
            "removed": [{"memory_id": str(memory_id), "reason": "never"}],
            "added_back": [],
        }
        response = _assert_json(
            await memory_client.post(
                "/v1/inject/commit",
                json=request,
            ),
            200,
        )
        assert response == {"final_block": CANONICAL_EMPTY_BLOCK, "wrong_removed": []}
        if index == 3:
            third_request = request

    assert third_request is not None
    repeated = _assert_json(
        await memory_client.post("/v1/inject/commit", json=third_request),
        200,
    )
    assert repeated == {"final_block": CANONICAL_EMPTY_BLOCK, "wrong_removed": []}

    async with memory_session_factory() as session:
        head = await session.get(MemoryUnit, memory_id)
        revisions = (
            await session.scalars(
                select(MemoryRevision)
                .where(MemoryRevision.memory_id == memory_id)
                .order_by(MemoryRevision.revision)
            )
        ).all()
        events = (
            await session.scalars(
                select(InjectionEvent)
                .where(InjectionEvent.memory_id == memory_id)
                .order_by(InjectionEvent.injection_id)
            )
        ).all()
    assert head is not None
    assert (head.status, head.stats["never_kills"], head.stats["removals"], head.revision) == (
        "quarantined",
        3,
        3,
        4,
    )
    assert head.bias == pytest.approx(-0.45)
    assert len(revisions) == 4
    assert (revisions[-1].reason, revisions[-1].origin_machine_id) == (
        "inject/commit:removed:never",
        "machine-gate",
    )
    assert [(event.shown_as, event.outcome) for event in events] == [
        ("injected", "removed:never"),
        ("injected", "removed:never"),
        ("near_miss", "removed:never"),
    ]

    prompt = "quarantined units stay out"
    embedding_provider.set(prompt, basis_vector(0))
    prepared = _assert_json(
        await memory_client.post(
            "/v1/inject/prepare",
            json={
                "thread_id": str(uuid4()),
                "agent_id": "agent-1",
                "machine_id": "machine-1",
                "principal_id": "owner",
                "project_key": "alpha",
                "prompt": prompt,
                "model_context_tokens": 100_000,
            },
        ),
        200,
    )
    assert prepared["injected"] == []
    assert prepared["near_misses"] == []
    zero_commit = _assert_json(
        await memory_client.post(
            "/v1/inject/commit",
            json={"injection_id": prepared["injection_id"], "removed": [], "added_back": []},
        ),
        200,
    )
    assert zero_commit == {"final_block": CANONICAL_EMPTY_BLOCK, "wrong_removed": []}
    unknown_id = uuid4()
    assert (
        _assert_json(
            await memory_client.post(
                "/v1/inject/commit",
                json={"injection_id": str(unknown_id), "removed": [], "added_back": []},
            ),
            200,
        )
        == zero_commit
    )
    _assert_problem(
        await memory_client.post(
            "/v1/inject/commit",
            json={
                "injection_id": str(unknown_id),
                "removed": [{"memory_id": str(memory_id), "reason": "wrong"}],
                "added_back": [],
            },
        ),
        404,
        "POST /v1/inject/commit",
    )


async def test_never_uses_event_scorer_version_and_preserves_terminal_status(
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-036 is defended by verifying that never uses event scorer version and preserves
    terminal status; this prevents drift in the gate feedback and citation-frequency
    contract.
    """
    historic_version = "historic-never"
    active_id, tombstoned_id = UUID(int=2101), UUID(int=2102)
    await _insert_memory(
        memory_session_factory,
        memory_id=active_id,
        label="Historic active",
        body="Uses historic scorer parameters",
    )
    await _insert_memory(
        memory_session_factory,
        memory_id=tombstoned_id,
        label="Historic tombstone",
        body="Must stay tombstoned",
        status="tombstoned",
    )
    async with memory_session_factory() as session:
        async with session.begin():
            session.add(
                ScorerConfig(
                    version=historic_version,
                    weights={},
                    params={"never_bias_step": -0.25, "quarantine_kills": 2},
                    active=False,
                )
            )

    injection_ids = (UUID(int=9151), UUID(int=9152))
    await _insert_event(
        memory_session_factory,
        injection_id=injection_ids[0],
        memory_id=active_id,
        rank=1,
        shown_as="injected",
        event_seed=151,
        label="Historic active",
        body="Uses historic scorer parameters",
        scorer_version=historic_version,
    )
    await _insert_event(
        memory_session_factory,
        injection_id=injection_ids[1],
        memory_id=active_id,
        rank=1,
        shown_as="injected",
        event_seed=152,
        label="Historic active",
        body="Uses historic scorer parameters",
        scorer_version=historic_version,
    )
    await _insert_event(
        memory_session_factory,
        injection_id=injection_ids[1],
        memory_id=tombstoned_id,
        rank=2,
        shown_as="injected",
        event_seed=153,
        label="Historic tombstone",
        body="Must stay tombstoned",
        scorer_version=historic_version,
    )

    for injection_id, removed_ids in (
        (injection_ids[0], (active_id,)),
        (injection_ids[1], (active_id, tombstoned_id)),
    ):
        response = await memory_client.post(
            "/v1/inject/commit",
            json={
                "injection_id": str(injection_id),
                "removed": [
                    {"memory_id": str(memory_id), "reason": "never"} for memory_id in removed_ids
                ],
                "added_back": [],
            },
        )
        assert _assert_json(response, 200) == {
            "final_block": CANONICAL_EMPTY_BLOCK,
            "wrong_removed": [],
        }

    async with memory_session_factory() as session:
        active = await session.get(MemoryUnit, active_id)
        tombstoned = await session.get(MemoryUnit, tombstoned_id)
    assert active is not None and tombstoned is not None
    assert (
        active.status,
        active.stats["never_kills"],
        active.stats["removals"],
        active.bias,
    ) == ("quarantined", 2, 2, pytest.approx(-0.5))
    assert (
        tombstoned.status,
        tombstoned.stats["never_kills"],
        tombstoned.stats["removals"],
        tombstoned.bias,
    ) == ("tombstoned", 1, 1, pytest.approx(-0.25))


async def test_commit_validation_and_concurrent_retry_are_atomic(
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A-036 is defended by verifying that commit validation and concurrent retry are atomic;
    this prevents drift in the gate feedback and citation-frequency contract.
    """
    injection_id = UUID(int=9201)
    injected_id, near_id = UUID(int=3001), UUID(int=3002)
    for memory_id, label in ((injected_id, "Injected"), (near_id, "Near")):
        await _insert_memory(
            memory_session_factory,
            memory_id=memory_id,
            label=label,
            body=f"{label} body",
        )
    await _insert_event(
        memory_session_factory,
        injection_id=injection_id,
        memory_id=injected_id,
        rank=1,
        shown_as="injected",
        event_seed=201,
        label="Injected",
        body="Injected body",
    )
    await _insert_event(
        memory_session_factory,
        injection_id=injection_id,
        memory_id=near_id,
        rank=2,
        shown_as="near_miss",
        event_seed=202,
        label="Near",
        body="Near body",
    )

    invalid_bodies = [
        {
            "removed": [
                {"memory_id": str(injected_id), "reason": "wrong"},
                {"memory_id": str(injected_id), "reason": "never"},
            ],
            "added_back": [],
        },
        {
            "removed": [{"memory_id": str(near_id), "reason": "wrong"}],
            "added_back": [],
        },
        {
            "removed": [{"memory_id": str(near_id), "reason": "not_relevant"}],
            "added_back": [],
        },
        {
            "removed": [{"memory_id": str(near_id), "reason": "never"}],
            "added_back": [str(near_id)],
        },
        {"removed": [], "added_back": [str(injected_id)]},
        {
            "removed": [{"memory_id": str(injected_id), "reason": "wrong"}],
            "added_back": [str(injected_id)],
        },
        {
            "removed": [{"memory_id": str(UUID(int=3999)), "reason": "wrong"}],
            "added_back": [],
        },
    ]
    for body in invalid_bodies:
        _assert_problem(
            await memory_client.post(
                "/v1/inject/commit",
                json={"injection_id": str(injection_id), **body},
            ),
            422,
            "POST /v1/inject/commit",
        )

    request = {
        "injection_id": str(injection_id),
        "removed": [{"memory_id": str(injected_id), "reason": "not_relevant"}],
        "added_back": [str(near_id)],
    }
    _force_first_lock_pair_to_overlap(monkeypatch)
    responses = await asyncio.gather(
        memory_client.post("/v1/inject/commit", json=request),
        memory_client.post("/v1/inject/commit", json=request),
    )
    payloads = [_assert_json(response, 200) for response in responses]
    assert payloads[0] == payloads[1]
    assert "Near body" in payloads[0]["final_block"]
    assert "Injected body" not in payloads[0]["final_block"]

    conflict = await memory_client.post(
        "/v1/inject/commit",
        json={"injection_id": str(injection_id), "removed": [], "added_back": [str(near_id)]},
    )
    _assert_problem(conflict, 409, "POST /v1/inject/commit")

    async with memory_session_factory() as session:
        events = (
            await session.scalars(
                select(InjectionEvent)
                .where(InjectionEvent.injection_id == injection_id)
                .order_by(InjectionEvent.rank)
            )
        ).all()
        injected = await session.get(MemoryUnit, injected_id)
        near = await session.get(MemoryUnit, near_id)
    assert [event.outcome for event in events] == ["removed:not_relevant", "added_back"]
    assert injected is not None and near is not None
    assert (injected.stats["removals"], injected.revision) == (1, 2)
    assert (near.stats["injections"], near.revision) == (1, 2)


async def test_cross_injection_addbacks_serialize_counter_and_last_timestamp(
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A-036 is defended by verifying that cross injection addbacks serialize counter and last
    timestamp; this prevents drift in the gate feedback and citation-frequency contract.
    """
    memory_id = UUID(int=3501)
    injection_ids = (UUID(int=9251), UUID(int=9252))
    await _insert_memory(
        memory_session_factory,
        memory_id=memory_id,
        label="Shared near miss",
        body="Selected by two independent gates",
    )
    for index, injection_id in enumerate(injection_ids, start=1):
        await _insert_event(
            memory_session_factory,
            injection_id=injection_id,
            memory_id=memory_id,
            rank=1,
            shown_as="near_miss",
            event_seed=250 + index,
            label="Shared near miss",
            body="Selected by two independent gates",
        )

    older = datetime(2026, 2, 1, tzinfo=UTC)
    newer = datetime(2026, 2, 2, tzinfo=UTC)
    first_sampled = asyncio.Event()
    second_sampled = asyncio.Event()
    release_first = asyncio.Event()
    calls = 0

    async def controlled_clock(_: AsyncSession) -> datetime:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_sampled.set()
            await asyncio.wait_for(release_first.wait(), timeout=2)
            return older
        second_sampled.set()
        return newer

    monkeypatch.setattr(decisions_module, "_database_clock", controlled_clock)

    def request(injection_id: UUID) -> dict[str, Any]:
        return {
            "injection_id": str(injection_id),
            "removed": [],
            "added_back": [str(memory_id)],
        }

    first_task = asyncio.create_task(
        memory_client.post("/v1/inject/commit", json=request(injection_ids[0]))
    )
    await asyncio.wait_for(first_sampled.wait(), timeout=2)
    second_task = asyncio.create_task(
        memory_client.post("/v1/inject/commit", json=request(injection_ids[1]))
    )
    try:
        await asyncio.wait_for(second_sampled.wait(), timeout=0.2)
        sampled_before_first_released = True
    except TimeoutError:
        sampled_before_first_released = False

    if sampled_before_first_released:
        second_response = await asyncio.wait_for(second_task, timeout=2)
        release_first.set()
        first_response = await asyncio.wait_for(first_task, timeout=2)
    else:
        release_first.set()
        first_response, second_response = await asyncio.gather(first_task, second_task)

    first_payload = _assert_json(first_response, 200)
    second_payload = _assert_json(second_response, 200)
    assert first_payload == second_payload
    assert "Shared near miss" in first_payload["final_block"]

    async with memory_session_factory() as session:
        head = await session.get(MemoryUnit, memory_id)
        events = (
            await session.scalars(
                select(InjectionEvent)
                .where(InjectionEvent.injection_id.in_(injection_ids))
                .order_by(InjectionEvent.injection_id)
            )
        ).all()
    assert head is not None
    assert (head.stats["injections"], head.stats["last_injected_at"], head.revision) == (
        2,
        newer.isoformat(),
        3,
    )
    assert [event.outcome for event in events] == ["added_back", "added_back"]


async def test_all_near_miss_empty_commit_is_repeatable_no_op(
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-036 is defended by verifying that all near miss empty commit is repeatable no op; this
    prevents drift in the gate feedback and citation-frequency contract.
    """
    injection_id, memory_id = UUID(int=9261), UUID(int=3601)
    await _insert_memory(
        memory_session_factory,
        memory_id=memory_id,
        label="Declined near miss",
        body="Never selected",
    )
    await _insert_event(
        memory_session_factory,
        injection_id=injection_id,
        memory_id=memory_id,
        rank=1,
        shown_as="near_miss",
        event_seed=261,
        label="Declined near miss",
        body="Never selected",
    )
    request = {"injection_id": str(injection_id), "removed": [], "added_back": []}

    for _ in range(2):
        assert _assert_json(await memory_client.post("/v1/inject/commit", json=request), 200) == {
            "final_block": CANONICAL_EMPTY_BLOCK,
            "wrong_removed": [],
        }

    async with memory_session_factory() as session:
        event = (
            await session.scalars(
                select(InjectionEvent).where(InjectionEvent.injection_id == injection_id)
            )
        ).one()
        head = await session.get(MemoryUnit, memory_id)
    assert event.outcome is None
    assert head is not None
    assert (head.stats, head.revision) == (DEFAULT_STATS, 1)


@pytest.mark.parametrize(
    "shown_as,outcome,accepted",
    [
        ("near_miss", None, True),
        ("near_miss", "removed:never", False),
        ("injected", None, False),
        ("injected", "mid_thread_removed", True),
    ],
)
async def test_post_gate_add_accepts_only_unused_near_miss_or_removed_member(
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
    shown_as,
    outcome,
    accepted,
) -> None:
    """ADR-018 / F101: trace adds log actual suggestions, never resurrect a veto or rescore."""
    injection_id, memory_id = uuid4(), uuid4()
    await _insert_memory(
        memory_session_factory, memory_id=memory_id, label="Trace", body="Trace body"
    )
    await _insert_event(
        memory_session_factory,
        injection_id=injection_id,
        memory_id=memory_id,
        rank=1,
        shown_as=shown_as,
        outcome=outcome,
        event_seed=301,
        label="Trace",
        body="Trace body",
    )
    request = {
        "injection_id": str(injection_id),
        "memory_id": str(memory_id),
        "signal": "mid_thread_added",
    }
    for _ in range(2):
        response = await memory_client.post("/v1/feedback", json=request)
        assert response.status_code == (200 if accepted else 409)
    async with memory_session_factory() as session:
        head = await session.get(MemoryUnit, memory_id)
        assert head is not None and head.revision == 1 and head.stats == DEFAULT_STATS
        event = await session.scalar(
            select(InjectionEvent).where(InjectionEvent.injection_id == injection_id)
        )
        assert event.outcome == ("mid_thread_added" if accepted else outcome)


async def test_feedback_is_exactly_once_and_cited_increments_frequency(
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C.4 feedback stays idempotent while A-036 activates citation frequency."""

    injection_id = UUID(int=9301)
    removed_id, cited_id, null_id, gate_removed_id = (
        UUID(int=value) for value in (4001, 4002, 4003, 4004)
    )
    for memory_id, label in (
        (removed_id, "Mid remove"),
        (cited_id, "Cited"),
        (null_id, "Unselected near"),
        (gate_removed_id, "Gate removed"),
    ):
        await _insert_memory(
            memory_session_factory,
            memory_id=memory_id,
            label=label,
            body=f"{label} body",
        )
    await _insert_event(
        memory_session_factory,
        injection_id=injection_id,
        memory_id=removed_id,
        rank=1,
        shown_as="injected",
        event_seed=301,
        label="Mid remove",
        body="Mid remove body",
        outcome="kept",
    )
    await _insert_event(
        memory_session_factory,
        injection_id=injection_id,
        memory_id=cited_id,
        rank=2,
        shown_as="near_miss",
        event_seed=302,
        label="Cited",
        body="Cited body",
        outcome="added_back",
    )
    await _insert_event(
        memory_session_factory,
        injection_id=injection_id,
        memory_id=null_id,
        rank=3,
        shown_as="near_miss",
        event_seed=303,
        label="Unselected near",
        body="Unselected near body",
    )
    await _insert_event(
        memory_session_factory,
        injection_id=injection_id,
        memory_id=gate_removed_id,
        rank=4,
        shown_as="injected",
        event_seed=304,
        label="Gate removed",
        body="Gate removed body",
        outcome="removed:wrong",
    )

    async with memory_session_factory() as session:
        async with session.begin():
            await cas_update_memory_unit(
                session,
                CasUpdate(
                    memory_id=removed_id,
                    expected_revision=1,
                    rev_uid=mint_ulid(),
                    editor="user",
                    origin_machine_id="patch-machine",
                    reason="current edit",
                    changes=MemoryUnitChanges(body="current edited body"),
                ),
            )

    removal_request = {
        "injection_id": str(injection_id),
        "memory_id": str(removed_id),
        "signal": "mid_thread_removed",
    }
    _force_first_lock_pair_to_overlap(monkeypatch)
    removal_responses = await asyncio.gather(
        memory_client.post("/v1/feedback", json=removal_request),
        memory_client.post("/v1/feedback", json=removal_request),
    )
    assert [_assert_json(response, 200) for response in removal_responses] == [
        {"ok": True},
        {"ok": True},
    ]

    cited_request = {
        "injection_id": str(injection_id),
        "memory_id": str(cited_id),
        "signal": "cited",
    }
    assert _assert_json(await memory_client.post("/v1/feedback", json=cited_request), 200) == {
        "ok": True
    }
    assert _assert_json(await memory_client.post("/v1/feedback", json=cited_request), 200) == {
        "ok": True
    }
    _assert_problem(
        await memory_client.post(
            "/v1/feedback",
            json={**cited_request, "signal": "mid_thread_removed"},
        ),
        409,
        "POST /v1/feedback",
    )
    for memory_id in (null_id, gate_removed_id):
        _assert_problem(
            await memory_client.post(
                "/v1/feedback",
                json={
                    "injection_id": str(injection_id),
                    "memory_id": str(memory_id),
                    "signal": "mid_thread_removed",
                },
            ),
            409,
            "POST /v1/feedback",
        )
    _assert_problem(
        await memory_client.post(
            "/v1/feedback",
            json={
                "injection_id": str(uuid4()),
                "memory_id": str(removed_id),
                "signal": "cited",
            },
        ),
        404,
        "POST /v1/feedback",
    )
    _assert_problem(
        await memory_client.post(
            "/v1/feedback",
            json={
                "injection_id": str(injection_id),
                "memory_id": str(UUID(int=4999)),
                "signal": "cited",
            },
        ),
        404,
        "POST /v1/feedback",
    )

    retry = _assert_json(
        await memory_client.post(
            "/v1/inject/commit",
            json={
                "injection_id": str(injection_id),
                "removed": [{"memory_id": str(gate_removed_id), "reason": "wrong"}],
                "added_back": [str(cited_id)],
            },
        ),
        200,
    )
    assert "Cited body" in retry["final_block"]
    assert "Mid remove body" not in retry["final_block"]
    assert [item["memory_id"] for item in retry["wrong_removed"]] == [str(gate_removed_id)]

    async with memory_session_factory() as session:
        events = {
            event.memory_id: event
            for event in (
                await session.scalars(
                    select(InjectionEvent).where(InjectionEvent.injection_id == injection_id)
                )
            ).all()
        }
        removed = await session.get(MemoryUnit, removed_id)
        cited = await session.get(MemoryUnit, cited_id)
        removed_revisions = (
            await session.scalars(
                select(MemoryRevision)
                .where(MemoryRevision.memory_id == removed_id)
                .order_by(MemoryRevision.revision)
            )
        ).all()
        cited_revisions = (
            await session.scalars(
                select(MemoryRevision)
                .where(MemoryRevision.memory_id == cited_id)
                .order_by(MemoryRevision.revision)
            )
        ).all()
    assert events[removed_id].outcome == "mid_thread_removed"
    assert events[cited_id].outcome == "cited"
    assert removed is not None and cited is not None
    assert (removed.body, removed.stats["removals"], removed.revision) == (
        "current edited body",
        1,
        3,
    )
    assert (cited.stats["citations"], cited.revision) == (1, 2)
    feedback_revision = removed_revisions[-1]
    assert (
        feedback_revision.parent_uid,
        feedback_revision.editor,
        feedback_revision.origin_machine_id,
        feedback_revision.reason,
    ) == (
        removed_revisions[-2].rev_uid,
        "system:feedback",
        "machine-gate",
        "feedback/mid_thread_removed",
    )
    assert (
        cited_revisions[-1].editor,
        cited_revisions[-1].origin_machine_id,
        cited_revisions[-1].reason,
    ) == ("system:feedback", "machine-gate", "feedback/cited")


@pytest.mark.asyncio
async def test_autonomous_entry_accepts_one_citation_transition(
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """An M2G auto-entered member can feed ADR-005 citation frequency. [A-036]"""

    injection_id = UUID(int=9391)
    memory_id = UUID(int=4391)
    await _insert_memory(
        memory_session_factory,
        memory_id=memory_id,
        label="Autonomous citation",
        body="Autonomous citation body",
    )
    await _insert_event(
        memory_session_factory,
        injection_id=injection_id,
        memory_id=memory_id,
        rank=1,
        shown_as="injected",
        event_seed=391,
        label="Autonomous citation",
        body="Autonomous citation body",
        outcome="auto_entered",
        actor_class="passive",
    )
    request = {
        "injection_id": str(injection_id),
        "memory_id": str(memory_id),
        "signal": "cited",
    }

    assert _assert_json(await memory_client.post("/v1/feedback", json=request), 200) == {"ok": True}
    assert _assert_json(await memory_client.post("/v1/feedback", json=request), 200) == {"ok": True}

    async with memory_session_factory() as session:
        event = (
            await session.scalars(
                select(InjectionEvent).where(
                    InjectionEvent.injection_id == injection_id,
                    InjectionEvent.memory_id == memory_id,
                )
            )
        ).one()
        head = await session.get(MemoryUnit, memory_id)
    assert event.outcome == "cited"
    assert head is not None
    assert (head.stats["citations"], head.revision) == (1, 2)


async def test_mid_thread_removed_can_be_human_readded_and_removed_again(
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-036 is defended by verifying that mid thread removed can be human readded and removed
    again; this prevents drift in the gate feedback and citation-frequency contract.
    """
    injection_id = UUID(int=9401)
    memory_id = UUID(int=4401)
    await _insert_memory(
        memory_session_factory,
        memory_id=memory_id,
        label="Toggle context",
        body="Toggle context body",
    )
    await _insert_event(
        memory_session_factory,
        injection_id=injection_id,
        memory_id=memory_id,
        rank=1,
        shown_as="injected",
        event_seed=401,
        label="Toggle context",
        body="Toggle context body",
        outcome="kept",
    )

    def feedback(signal: str) -> dict[str, str]:
        return {
            "injection_id": str(injection_id),
            "memory_id": str(memory_id),
            "signal": signal,
        }

    for signal in ("mid_thread_removed", "mid_thread_added", "mid_thread_added"):
        assert _assert_json(
            await memory_client.post("/v1/feedback", json=feedback(signal)), 200
        ) == {"ok": True}

    async with memory_session_factory() as session:
        after_add = await session.get(MemoryUnit, memory_id)
    assert after_add is not None
    assert after_add.stats["removals"] == 1

    assert _assert_json(
        await memory_client.post("/v1/feedback", json=feedback("mid_thread_removed")),
        200,
    ) == {"ok": True}
    async with memory_session_factory() as session:
        event = (
            await session.scalars(
                select(InjectionEvent).where(
                    InjectionEvent.injection_id == injection_id,
                    InjectionEvent.memory_id == memory_id,
                )
            )
        ).one()
        head = await session.get(MemoryUnit, memory_id)
    assert event.outcome == "mid_thread_removed"
    assert head is not None and head.stats["removals"] == 2


async def test_lineage_failures_roll_back_commit_and_feedback(
    memory_app: Any,
    memory_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A-036 is defended by verifying that lineage failures roll back commit and feedback; this
    prevents drift in the gate feedback and citation-frequency contract.
    """
    successful_commit_id, commit_id, feedback_id = (
        UUID(int=5000),
        UUID(int=5001),
        UUID(int=5002),
    )
    commit_injection, feedback_injection = UUID(int=9401), UUID(int=9402)
    await _insert_memory(
        memory_session_factory,
        memory_id=successful_commit_id,
        label="First commit effect",
        body="must roll back",
    )
    await _insert_memory(
        memory_session_factory,
        memory_id=commit_id,
        label="Broken commit lineage",
        body="unchanged",
        with_root=False,
    )
    await _insert_memory(
        memory_session_factory,
        memory_id=feedback_id,
        label="Feedback outer rollback",
        body="unchanged",
    )
    await _insert_event(
        memory_session_factory,
        injection_id=commit_injection,
        memory_id=successful_commit_id,
        rank=1,
        shown_as="injected",
        event_seed=400,
        label="First commit effect",
        body="must roll back",
    )
    await _insert_event(
        memory_session_factory,
        injection_id=commit_injection,
        memory_id=commit_id,
        rank=2,
        shown_as="injected",
        event_seed=401,
        label="Broken commit lineage",
        body="unchanged",
    )
    await _insert_event(
        memory_session_factory,
        injection_id=feedback_injection,
        memory_id=feedback_id,
        rank=1,
        shown_as="injected",
        event_seed=402,
        label="Feedback outer rollback",
        body="unchanged",
        outcome="kept",
    )

    async with AsyncClient(
        transport=ASGITransport(app=memory_app, raise_app_exceptions=False),
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        commit_response = await client.post(
            "/v1/inject/commit",
            json={
                "injection_id": str(commit_injection),
                "removed": [
                    {
                        "memory_id": str(successful_commit_id),
                        "reason": "not_relevant",
                    },
                    {"memory_id": str(commit_id), "reason": "not_relevant"},
                ],
                "added_back": [],
            },
        )

        async def fail_after_feedback_cas(*_: Any, **__: Any) -> None:
            raise RuntimeError("forced event outcome failure")

        monkeypatch.setattr(decisions_module, "_replace_outcome", fail_after_feedback_cas)
        feedback_response = await client.post(
            "/v1/feedback",
            json={
                "injection_id": str(feedback_injection),
                "memory_id": str(feedback_id),
                "signal": "mid_thread_removed",
            },
        )
    _assert_problem(commit_response, 500, "POST /v1/inject/commit")
    _assert_problem(feedback_response, 500, "POST /v1/feedback")

    async with memory_session_factory() as session:
        successful_commit_head = await session.get(MemoryUnit, successful_commit_id)
        commit_head = await session.get(MemoryUnit, commit_id)
        feedback_head = await session.get(MemoryUnit, feedback_id)
        commit_events = (
            await session.scalars(
                select(InjectionEvent)
                .where(InjectionEvent.injection_id == commit_injection)
                .order_by(InjectionEvent.rank)
            )
        ).all()
        feedback_event = (
            await session.scalars(
                select(InjectionEvent).where(InjectionEvent.injection_id == feedback_injection)
            )
        ).one()
        revision_counts = {
            memory_id: await session.scalar(
                select(func.count())
                .select_from(MemoryRevision)
                .where(MemoryRevision.memory_id == memory_id)
            )
            for memory_id in (successful_commit_id, commit_id, feedback_id)
        }
    assert successful_commit_head is not None
    assert commit_head is not None and feedback_head is not None
    assert (successful_commit_head.revision, successful_commit_head.stats) == (1, DEFAULT_STATS)
    assert (commit_head.revision, commit_head.stats) == (1, DEFAULT_STATS)
    assert (feedback_head.revision, feedback_head.stats) == (1, DEFAULT_STATS)
    assert [event.outcome for event in commit_events] == [None, None]
    assert feedback_event.outcome == "kept"
    assert revision_counts == {
        successful_commit_id: 1,
        commit_id: 0,
        feedback_id: 1,
    }
