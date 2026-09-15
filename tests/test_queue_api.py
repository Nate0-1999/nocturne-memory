"""Live-Postgres proof for M2H queue birth, invisibility, and decisions."""

from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from conftest import ScriptedEmbeddingProvider, basis_vector, vector_with_cosine
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spine.db.models import (
    ApprovalDecision,
    ApprovalQueueItem,
    MemoryEdge,
    MemoryRevision,
    MemoryUnit,
)


def extraction(thread_id, candidate, *, verdict="new", target_ids=None):
    return {
        "principal_id": "owner",
        "thread_id": str(thread_id),
        "machine_id": "mac",
        "editor": "extraction",
        "candidates": [
            {
                "label": candidate,
                "body": candidate,
                "kind": "procedure",
                "keywords": ["queue", "consent"],
                "verdict": verdict,
                "target_ids": [str(value) for value in target_ids or []],
            }
        ],
    }


@pytest.mark.asyncio
async def test_rejected_seed_hash_survives_a_new_upload_identity(
    memory_client: AsyncClient,
    embedding_provider: ScriptedEmbeddingProvider,
) -> None:
    """M3MP / F089: unchanged rejected sources cannot acquire fresh queue cards."""
    markdown = "The fixture review day is Saturday."
    embedding_provider.set(markdown, basis_vector(0))
    payload = {
        "principal_id": "owner",
        "batch_uid": str(uuid4()),
        "source_name": "first.md",
        "source_sha256": sha256(markdown.encode()).hexdigest(),
        "markdown": markdown,
        "machine_id": "mac",
        "editor": "seed-splitter",
        "candidates": [
            {
                "label": "Review day",
                "body": markdown,
                "kind": "fact",
                "keywords": ["review", "day"],
                "verdict": "new",
                "target_ids": [],
            }
        ],
    }
    born = await memory_client.post("/v1/seeds", json=payload)
    assert born.status_code == 200 and len(born.json()["cards"]) == 1
    denied = await memory_client.post(
        f"/v1/approval-queue/batches/{payload['batch_uid']}/decisions",
        json={
            "decision": "deny",
            "approval_mode": "explicit",
            "actor_class": "human",
            "machine_id": "mac",
        },
    )
    assert denied.status_code == 200
    payload.update(batch_uid=str(uuid4()), source_name="renamed.md")
    repeated = await memory_client.post("/v1/seeds", json=payload)
    assert repeated.status_code == 200
    assert repeated.json()["cards"] == []
    assert repeated.json()["duplicate_count"] == 1
    payload.update(principal_id="another-fixture", batch_uid=str(uuid4()))
    other = await memory_client.post("/v1/seeds", json=payload)
    assert other.status_code == 200 and len(other.json()["cards"]) == 1
    changed = "The fixture review day is Sunday."
    embedding_provider.set(changed, basis_vector(1))
    payload.update(
        principal_id="owner",
        batch_uid=str(uuid4()),
        markdown=changed,
        source_sha256=sha256(changed.encode()).hexdigest(),
    )
    payload["candidates"][0].update(body=changed)
    revised = await memory_client.post("/v1/seeds", json=payload)
    assert revised.status_code == 200 and len(revised.json()["cards"]) == 1


@pytest.mark.asyncio
async def test_candidate_is_queue_only_and_denial_is_revisioned_signal(
    memory_client: AsyncClient,
    embedding_provider: ScriptedEmbeddingProvider,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-032 is defended by verifying that candidate is queue only and denial is revisioned
    signal; this prevents drift in the unified queue decision and lineage contract.
    """
    thread_id = uuid4()
    embedding_provider.set("candidate lesson", basis_vector(0))
    embedding_provider.set("find candidate", basis_vector(0))

    born = await memory_client.post(
        "/v1/extractions", json=extraction(thread_id, "candidate lesson")
    )
    assert born.status_code == 200
    card = born.json()["cards"][0]
    assert card["candidate"]["status"] == "candidate"
    assert card["candidate"]["origin_thread_id"] == str(thread_id)

    listed = await memory_client.get("/v1/memories")
    searched = await memory_client.post(
        "/v1/search", json={"principal_id": "owner", "query": "find candidate"}
    )
    assert listed.json()["items"] == []
    assert searched.json()["results"] == []

    denied = await memory_client.post(
        f"/v1/approval-queue/{card['item_uid']}/decisions",
        json={
            "decision": "deny",
            "approval_mode": "explicit",
            "actor_class": "human",
            "machine_id": "mac",
        },
    )
    assert denied.status_code == 200
    assert denied.json()["card"]["candidate"]["status"] == "tombstoned"
    async with memory_session_factory() as session:
        row = await session.scalar(
            select(MemoryUnit).where(MemoryUnit.id == card["candidate"]["memory_id"])
        )
        decision_count = await session.scalar(select(func.count()).select_from(ApprovalDecision))
    assert row is not None and row.revision == 2 and row.status == "tombstoned"
    assert row.origin_thread_id == thread_id
    assert decision_count == 1


@pytest.mark.asyncio
async def test_merge_approval_activates_candidate_tombstones_target_and_is_idempotent(
    memory_client: AsyncClient,
    embedding_provider: ScriptedEmbeddingProvider,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-032 is defended by verifying that merge approval activates candidate tombstones target
    and is idempotent; this prevents drift in the unified queue decision and lineage
    contract.
    """
    thread_id = uuid4()
    embedding_provider.set("old lesson", basis_vector(0))
    embedding_provider.set("merged lesson", vector_with_cosine(0.85))
    created = await memory_client.post(
        "/v1/memories",
        json={
            "principal_id": "owner",
            "label": "old",
            "body": "old lesson",
            "kind": "procedure",
            "editor": "human",
            "machine_id": "mac",
        },
    )
    target_id = created.json()["created"]["memory_id"]

    born = await memory_client.post(
        "/v1/extractions",
        json=extraction(thread_id, "merged lesson", verdict="merge", target_ids=[target_id]),
    )
    card = born.json()["cards"][0]
    body = {
        "decision": "approve",
        "approval_mode": "explicit",
        "actor_class": "human",
        "machine_id": "mac",
    }
    first = await memory_client.post(f"/v1/approval-queue/{card['item_uid']}/decisions", json=body)
    second = await memory_client.post(f"/v1/approval-queue/{card['item_uid']}/decisions", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["decision_uid"] == second.json()["decision_uid"]
    async with memory_session_factory() as session:
        candidate = await session.get(MemoryUnit, card["candidate"]["memory_id"])
        target = await session.get(MemoryUnit, target_id)
        edge_count = await session.scalar(select(func.count()).select_from(MemoryEdge))
        pending = await session.scalar(
            select(func.count())
            .select_from(ApprovalQueueItem)
            .where(ApprovalQueueItem.state == "pending")
        )
    assert candidate is not None and candidate.status == "active"
    assert target is not None and target.status == "tombstoned"
    assert edge_count == 1
    assert pending == 0


@pytest.mark.asyncio
async def test_contradiction_cannot_passively_approve(
    memory_client: AsyncClient,
    embedding_provider: ScriptedEmbeddingProvider,
) -> None:
    """A-032 is defended by verifying that contradiction cannot passively approve; this
    prevents drift in the unified queue decision and lineage contract.
    """
    thread_id = uuid4()
    embedding_provider.set("first claim", basis_vector(0))
    embedding_provider.set("opposite claim", vector_with_cosine(0.85))
    target = await memory_client.post(
        "/v1/memories",
        json={
            "principal_id": "owner",
            "label": "first",
            "body": "first claim",
            "kind": "fact",
            "editor": "human",
            "machine_id": "mac",
        },
    )
    target_id = target.json()["created"]["memory_id"]
    born = await memory_client.post(
        "/v1/extractions",
        json=extraction(thread_id, "opposite claim", verdict="contradict", target_ids=[target_id]),
    )
    item_uid = born.json()["cards"][0]["item_uid"]
    response = await memory_client.post(
        f"/v1/approval-queue/{item_uid}/decisions",
        json={
            "decision": "approve",
            "approval_mode": "passive",
            "actor_class": "passive",
            "machine_id": "mac",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_seed_batch_preserves_split_lineage_and_decides_atomically(
    memory_client: AsyncClient,
    embedding_provider: ScriptedEmbeddingProvider,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-032 is defended by verifying that seed batch preserves split lineage and decides
    atomically; this prevents drift in the unified queue decision and lineage contract.
    """
    markdown = "# Durable notes\n\nAlpha stands alone. Beta stands alone."
    batch_uid = uuid4()
    embedding_provider.set(markdown, basis_vector(0))
    embedding_provider.set("Alpha stands alone.", basis_vector(1))
    embedding_provider.set("Beta stands alone.", basis_vector(2))
    payload = {
        "principal_id": "owner",
        "batch_uid": str(batch_uid),
        "source_name": "durable-notes.md",
        "source_sha256": sha256(markdown.encode()).hexdigest(),
        "markdown": markdown,
        "machine_id": "mac",
        "editor": "seed-splitter",
        "candidates": [
            {
                "label": "Alpha",
                "body": "Alpha stands alone.",
                "kind": "fact",
                "keywords": ["alpha", "standalone"],
                "verdict": "new",
                "target_ids": [],
            },
            {
                "label": "Beta",
                "body": "Beta stands alone.",
                "kind": "fact",
                "keywords": ["beta", "standalone"],
                "verdict": "new",
                "target_ids": [],
            },
        ],
    }

    born = await memory_client.post("/v1/seeds", json=payload)
    replay = await memory_client.post("/v1/seeds", json=payload)

    assert born.status_code == replay.status_code == 200
    assert born.json() == replay.json()
    cards = born.json()["cards"]
    assert len(cards) == 2
    assert {card["birthplace"] for card in cards} == {"seed"}
    assert {card["batch_uid"] for card in cards} == {str(batch_uid)}
    assert {card["birthplace_thread_id"] for card in cards} == {None}

    thread_only = await memory_client.get(
        "/v1/approval-queue",
        params={"principal_id": "owner", "birthplace": "thread"},
    )
    seed_only = await memory_client.get(
        "/v1/approval-queue",
        params={"principal_id": "owner", "birthplace": "seed"},
    )
    assert thread_only.json()["cards"] == []
    assert len(seed_only.json()["cards"]) == 2

    decision = await memory_client.post(
        f"/v1/approval-queue/batches/{batch_uid}/decisions",
        json={
            "decision": "approve",
            "approval_mode": "explicit",
            "actor_class": "human",
            "machine_id": "mac",
        },
    )
    assert decision.status_code == 200
    assert {card["state"] for card in decision.json()["cards"]} == {"approved"}

    child_ids = [UUID(card["candidate"]["memory_id"]) for card in cards]
    async with memory_session_factory() as session:
        source = (
            await session.execute(
                select(MemoryUnit).where(MemoryUnit.thread_origin == f"seed:{batch_uid}")
            )
        ).scalar_one()
        source_revision = (
            await session.execute(
                select(MemoryRevision).where(MemoryRevision.memory_id == source.id)
            )
        ).scalar_one()
        child_revisions = (
            (
                await session.execute(
                    select(MemoryRevision).where(
                        MemoryRevision.memory_id.in_(child_ids),
                        MemoryRevision.revision == 1,
                    )
                )
            )
            .scalars()
            .all()
        )
        relates = (
            (await session.execute(select(MemoryEdge).where(MemoryEdge.edge_type == "relates_to")))
            .scalars()
            .all()
        )
    assert source.status == "tombstoned"
    assert source.body == markdown
    assert {revision.parent_uid for revision in child_revisions} == {source_revision.rev_uid}
    assert {(edge.from_memory_id, edge.to_memory_id) for edge in relates} == {
        (child_ids[0], child_ids[1]),
        (child_ids[1], child_ids[0]),
    }
