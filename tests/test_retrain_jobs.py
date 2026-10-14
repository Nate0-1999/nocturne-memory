"""M5LN/F212: the manual fit belongs to a durable job, not its HTTP caller."""

import asyncio

import pytest
from conftest import TOKEN
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from spine.db.models import LearnerRun


async def test_job_returns_before_fit_and_survives_worker_restart(
    memory_app, memory_session_factory, monkeypatch
):
    """PLAN M5LN/F212: receipt polling survives a lost request and worker interruption."""
    service = memory_app.state.learner_service
    started, release = asyncio.Event(), asyncio.Event()
    fit = service._fit

    async def slow_fit(*args, **kwargs):
        started.set()
        await release.wait()
        return await fit(*args, **kwargs)

    monkeypatch.setattr(service, "_fit", slow_fit)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    params = {"principal_id": "local"}
    async with AsyncClient(
        transport=ASGITransport(app=memory_app), base_url="http://test"
    ) as client:
        assert (await client.post("/v1/retrain-jobs", params=params)).status_code == 401
        assert (await client.post("/v1/retrain-jobs", headers=headers)).status_code == 403
        response = await client.post("/v1/retrain-jobs", params=params, headers=headers)
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        assert response.json()["status"] == "pending"
    # The initiating client is gone before the fit starts.
    task = asyncio.create_task(service.run_pending_jobs())
    await asyncio.wait_for(started.wait(), timeout=2)
    assert (await service.get_job(job_id)).status == "pending"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    await service.run_pending_jobs()
    async with AsyncClient(
        transport=ASGITransport(app=memory_app), base_url="http://test"
    ) as client:
        response = await client.get(f"/v1/retrain-jobs/{job_id}", params=params, headers=headers)
        assert response.status_code == 200
        job = response.json()
        assert job["status"] == "completed"
        assert job["result"]["status"] == "insufficient_data"
        assert (await client.get(f"/v1/retrain-jobs/{job_id}", headers=headers)).status_code == 403
        assert (
            await client.get("/v1/retrain-jobs/missing", params=params, headers=headers)
        ).status_code == 404
    await service.run_pending_jobs()
    async with memory_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(LearnerRun)) == 1
        receipt = (await session.scalars(select(LearnerRun))).one()
        assert receipt.reason == job["result"]["reason"]
