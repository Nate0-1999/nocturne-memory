"""A-057 transcript backup and resurrection contract proofs."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _record(thread_id: UUID, sequence: int, text_value: str = "hello") -> dict[str, object]:
    line = json.dumps(
        {
            "captured_at": datetime(2026, 8, 14, sequence, tzinfo=UTC).isoformat(),
            "message": {"role": "user", "content": text_value},
            "thread_id": str(thread_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "thread_id": str(thread_id),
        "sequence": sequence,
        "journal_line": line,
        "sha256": hashlib.sha256(line.encode()).hexdigest(),
    }


@pytest.mark.asyncio
async def test_append_replay_list_and_status_are_exact(memory_client: AsyncClient) -> None:
    """A-057 preserves exact journal bytes and makes retries idempotent."""
    thread_id = UUID(int=5701)
    body = {"principal_id": "owner", "records": [_record(thread_id, 1), _record(thread_id, 2)]}

    first = await memory_client.post("/v1/transcripts", json=body)
    replay = await memory_client.post("/v1/transcripts", json=body)
    listing = await memory_client.get("/v1/transcripts", params={"principal_id": "owner"})
    status = await memory_client.get(
        "/v1/transcripts/status", params={"principal_id": "owner"}
    )

    assert first.status_code == 200
    assert first.json()["accepted"] == 2
    assert replay.json()["replayed"] == 2
    assert [row["journal_line"] for row in listing.json()["records"]] == [
        record["journal_line"] for record in body["records"]
    ]
    assert status.json()["thread_count"] == 1
    assert status.json()["record_count"] == 2
    assert status.json()["latest_received_at"] is not None


@pytest.mark.asyncio
async def test_changed_replay_or_gap_refuses_the_whole_batch(memory_client: AsyncClient) -> None:
    """A-057 refuses divergence and gaps without partially appending a batch."""
    thread_id = UUID(int=5702)
    initial = await memory_client.post(
        "/v1/transcripts", json={"principal_id": "owner", "records": [_record(thread_id, 1)]}
    )
    changed = await memory_client.post(
        "/v1/transcripts",
        json={"principal_id": "owner", "records": [_record(thread_id, 1, "changed")]},
    )
    gap = await memory_client.post(
        "/v1/transcripts",
        json={
            "principal_id": "owner",
            "records": [_record(thread_id, 2), _record(UUID(int=5703), 2)],
        },
    )
    listing = await memory_client.get("/v1/transcripts", params={"principal_id": "owner"})

    assert initial.status_code == 200
    assert changed.status_code == 409
    assert gap.status_code == 409
    assert len(listing.json()["records"]) == 1


@pytest.mark.asyncio
async def test_validation_and_database_both_defend_immutability(
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-057 rejects corrupted wire rows and database UPDATE/DELETE mutation."""
    thread_id = UUID(int=5704)
    record = _record(thread_id, 1)
    corrupt = dict(record)
    corrupt["sha256"] = "0" * 64
    response = await memory_client.post(
        "/v1/transcripts", json={"principal_id": "owner", "records": [corrupt]}
    )
    assert response.status_code == 422

    await memory_client.post(
        "/v1/transcripts", json={"principal_id": "owner", "records": [record]}
    )
    async with memory_session_factory() as session:
        with pytest.raises(DBAPIError, match="append-only"):
            async with session.begin():
                await session.execute(
                    text(
                        "UPDATE transcript_record SET journal_line = '{}' "
                        "WHERE principal_id = 'owner'"
                    )
                )
