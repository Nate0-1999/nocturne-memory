"""FL-172 archive round trips on real PostgreSQL."""

import json
from uuid import uuid4

from conftest import basis_vector

from spine.ids import mint_ulid


async def test_archive_retains_lineage_and_refuses_partial_conflicting_import(
    memory_client,
    embedding_provider,
):
    """A-071 preserves inactive source heads, child edges and revisions atomically."""
    source = "Alpha and beta are separate facts."
    embedding_provider.set(source, basis_vector(0))
    embedding_provider.set("Alpha is first.", basis_vector(1))
    embedding_provider.set("Beta is second.", basis_vector(2))
    response = await memory_client.post(
        "/v1/memory-splits",
        json={
            "principal_id": "source",
            "source_body": source,
            "editor": "user",
            "machine_id": "fixture",
            "children": [
                {"label": "Alpha", "body": "Alpha is first.", "keywords": ["alpha", "first"]},
                {"label": "Beta", "body": "Beta is second.", "keywords": ["beta", "second"]},
            ],
        },
    )
    assert response.status_code == 201, response.text
    archive = (
        await memory_client.get("/v1/memories/export", params={"principal_id": "source"})
    ).json()
    assert len(archive["memories"]) == 3
    assert len(archive["edges"]) == 2
    assert archive["revisions"]
    empty = await memory_client.get("/v1/memories/export", params={"principal_id": "stranger"})
    assert empty.json()["memories"] == []
    # Disjoint IDs represent another Palace without replacing the test database.
    encoded = json.dumps(archive)
    for row in archive["memories"]:
        encoded = encoded.replace(row["id"], str(uuid4()))
    for row in archive["revisions"]:
        encoded = encoded.replace(row["rev_uid"], mint_ulid())
    for row in archive["edges"]:
        encoded = encoded.replace(row["edge_uid"], mint_ulid())
    copied = json.loads(encoded)
    copied["principal_id"] = "destination"
    for row in (*copied["memories"], *copied["queue"]):
        row["principal_id"] = "destination"
    response = await memory_client.post(
        "/v1/memories/import", params={"principal_id": "destination"}, json=copied
    )
    assert response.status_code == 200, response.text
    restored = (
        await memory_client.get("/v1/memories/export", params={"principal_id": "destination"})
    ).json()
    for key in ("memories", "revisions", "edges", "queue", "decisions"):
        assert sorted(restored[key], key=str) == sorted(copied[key], key=str)
    for principal in ("destination", "stranger"):
        response = await memory_client.post(
            "/v1/memories/import", params={"principal_id": principal}, json=copied
        )
        assert response.status_code == 409
    after = await memory_client.get("/v1/memories/export", params={"principal_id": "destination"})
    assert after.json() == restored
