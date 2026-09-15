"""M3SC / F094: scope money and state before aggregation."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_spend import _event
from test_vitals import _insert_memory_heads

from spine.db.models import MemoryUnit
from spine.spend.views import SpendViewRefresher


async def test_metrics_keep_principal_rows_and_require_owner_for_palace(
    memory_client: AsyncClient,
    memory_app: FastAPI,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """SPEC C.4 / PLAN M3SC: foreign thread IDs cannot widen money or state reads."""
    principal = "nocturne-verification-m3sc"
    now = datetime.now(UTC)
    own = _event(ts=now.isoformat(), cost_usd="0.25")
    own["principal_id"] = principal
    foreign = _event("01K1M2A0000000000000000002", ts=now.isoformat(), cost_usd="9.00")
    unowned = _event("01K1M2A0000000000000000003", ts=now.isoformat(), cost_usd="5.00")
    unowned["principal_id"] = None
    assert (await memory_client.post(
        "/v1/spend/events", json={"events": [own, foreign, unowned]}
    )).status_code == 200
    await _insert_memory_heads(memory_session_factory, anchor=now)
    async with memory_session_factory() as session, session.begin():
        await session.execute(
            update(MemoryUnit).where(MemoryUnit.id == UUID(int=8101))
            .values(principal_id=principal)
        )
    await SpendViewRefresher(memory_session_factory).refresh_once()

    params = {"principal_id": principal}
    table = await memory_client.get("/v1/spend/table", params=params)
    assert table.status_code == 200
    assert float(table.json()["threads"][0]["total_usd"]) == 0.25
    endpoints = ["/v1/vitals", f"/v1/vitals/threads/{own['thread_id']}"]
    for endpoint in endpoints:
        response = await memory_client.get(endpoint, params=params)
        assert response.status_code == 200
        snapshot = response.json()
        assert float(snapshot["spend"]["lanes"][0]["points"][0]["cost_usd"]) == 0.25
        counts = {row["metric"]: row["count"] for row in snapshot["palace_counts"]}
        assert counts["active_units"] == counts["pinned_units"] == 1
        assert snapshot["lifecycle_rates"][0]["per_hour"] == 1
        assert snapshot["resources"]["database_bytes"] is None
        assert snapshot["reconciliation"]["broker_usage_usd"] is None

    for endpoint in ["/v1/spend/table", *endpoints]:
        denied = await memory_client.get(endpoint, params={**params, "scope": "palace"})
        assert denied.status_code == 403
        assert denied.json()["detail"] == "Only the Palace owner can view the whole Palace."
        assert (await memory_client.get(endpoint)).status_code == 422

    # The owner is explicit deployment configuration, not a guessed principal prefix.
    memory_app.state.settings.owner_principal_id = "custom-owner"
    owner = {"principal_id": "custom-owner", "scope": "palace"}
    palace = await memory_client.get("/v1/spend/table", params=owner)
    assert float(palace.json()["threads"][0]["total_usd"]) == 14.25
    scoped_owner = await memory_client.get(
        "/v1/spend/table", params={"principal_id": "custom-owner"}
    )
    assert scoped_owner.json()["threads"] == []
    identity = await memory_client.get("/v1/identity", params={"principal_id": "custom-owner"})
    assert identity.json() == {"principal_id": "custom-owner", "is_owner": True}
    palace_state = (await memory_client.get("/v1/vitals", params=owner)).json()
    assert palace_state["resources"]["database_bytes"] > 0
    counts = {row["metric"]: row["count"] for row in palace_state["palace_counts"]}
    assert counts["active_units"] == 2
