"""SD-059 / PLAN M3SJ: durable jobs survive service lifetimes without duplicate runs."""

from uuid import uuid4

import pytest
from sqlalchemy import text

from spine.ids import mint_ulid


@pytest.mark.asyncio
async def test_saved_recipe_claims_are_atomic_and_scoped(memory_client, memory_session_factory):
    """SPEC C.4 / M3SJ: replay, concurrent claim, scope and restart preserve one execution."""
    job_id = mint_ulid()
    owner = {"principal_id": "jobs-verification", "machine_id": "jobs-machine"}
    definition = {
        "name": "Check",
        "prompt": "Write hello",
        "folder": "/tmp",
        "model_policy": "pinned:openai/gpt-4.1-nano",
        "budget_usd": "0.1",
        "exit_condition": "test -f hello",
        "cron": "* * * * *",
    }
    saved = await memory_client.put(
        f"/v1/jobs/{job_id}",
        json={
            **owner,
            "definition": definition,
            "expected_revision": 0,
        },
    )
    assert saved.status_code == 200, saved.text
    body = {
        **owner,
        "run_id": mint_ulid(),
        "thread_id": str(uuid4()),
        "trigger_key": "manual-1",
        "expected_revision": 1,
        "manual": True,
    }
    first = await memory_client.post(f"/v1/jobs/{job_id}/runs", json=body)
    assert first.status_code == 200, first.text
    assert (await memory_client.post(f"/v1/jobs/{job_id}/runs", json=body)).status_code == 409
    hidden = await memory_client.get("/v1/jobs", params={**owner, "principal_id": "other"})
    assert hidden.json() == {"jobs": [], "runs": []}
    assert (
        await memory_client.post(
            f"/v1/job-runs/{body['run_id']}/finish",
            json={
                **owner,
                "principal_id": "other",
                "state": "completed",
                "verdict": "wrong owner",
            },
        )
    ).status_code == 409
    finish = await memory_client.post(
        f"/v1/job-runs/{body['run_id']}/finish",
        json={
            **owner,
            "state": "completed",
            "verdict": "Exit check passed.",
        },
    )
    assert finish.status_code == 200
    from spine.jobs import JobService

    restarted = JobService(memory_session_factory)
    restored = await restarted.list(**owner)
    assert restored["runs"][0]["definition"] == saved.json()["definition"]
    assert restored["runs"][0]["state"] == "completed"
    async with memory_session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE workflow_job SET next_run_at=now()-interval '1 minute' WHERE job_id=:id"),
            {"id": job_id},
        )
    due = (await restarted.list(**owner))["jobs"][0]
    body.update(
        run_id=mint_ulid(),
        thread_id=str(uuid4()),
        manual=False,
        trigger_key=due["next_run_at"].isoformat(),
    )
    assert (await memory_client.post(f"/v1/jobs/{job_id}/runs", json=body)).status_code == 200
    assert (await memory_client.post(f"/v1/jobs/{job_id}/runs", json=body)).status_code == 409


@pytest.mark.asyncio
async def test_bad_schedule_does_not_save(memory_client):
    """SPEC C.4 / M3SJ: a cron typo is caught at authoring time, before daemon work."""
    result = await memory_client.put(
        f"/v1/jobs/{mint_ulid()}",
        json={
            "principal_id": "jobs-verification",
            "machine_id": "machine",
            "expected_revision": 0,
            "definition": {
                "name": "Bad cron",
                "prompt": "hello",
                "folder": "/tmp",
                "model_policy": "elbow",
                "budget_usd": "0.1",
                "exit_condition": "true",
                "cron": "every minute",
            },
        },
    )
    assert result.status_code == 422
