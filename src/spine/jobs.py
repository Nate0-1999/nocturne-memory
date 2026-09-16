"""M3SJ / SD-059: named recipes and durable execution cursors; one spend ledger."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from croniter import croniter
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text

from spine.contracts import ULID


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    folder: str = Field(min_length=1)
    model_policy: str = Field(min_length=1)
    tools: Literal["pydantic", "none"] = "pydantic"
    memory_scope: Literal["workspace", "none"] = "workspace"
    budget_usd: Decimal = Field(gt=0)
    exit_condition: str = Field(min_length=1)
    cron: str | None = None
    trigger: Literal["file", "queue", "palace"] | None = None
    trigger_path: str | None = None

    @field_validator("name", "prompt", "folder", "model_policy", "exit_condition")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Workflow fields cannot be blank.")
        return value

    @field_validator("cron")
    @classmethod
    def cron_line(cls, value: str | None) -> str | None:
        if value is not None:
            if len(value.split()) != 5 or not croniter.is_valid(value):
                raise ValueError("Use a five-field cron line in UTC.")
            croniter(value, datetime.now(UTC)).get_next(datetime)
        return value

    @model_validator(mode="after")
    def schedule_shape(self):
        if self.cron and self.trigger:
            raise ValueError("Choose a cron line or an event trigger.")
        if (self.trigger == "file") != bool(self.trigger_path):
            raise ValueError("A file trigger requires its file path.")
        return self


class SaveJob(BaseModel):
    principal_id: str
    machine_id: str
    definition: WorkflowDefinition
    expected_revision: int = Field(ge=0)
    enabled: bool = True
    trigger_cursor: str | None = None


class ClaimJob(BaseModel):
    principal_id: str
    machine_id: str
    run_id: ULID
    thread_id: UUID
    trigger_key: str
    expected_revision: int
    trigger_cursor: str | None = None
    manual: bool = False


class FinishJob(BaseModel):
    principal_id: str
    machine_id: str
    state: Literal["completed", "failed", "cancelled", "interrupted"]
    verdict: str


class JobService:
    def __init__(self, sessions):
        self.sessions = sessions

    async def list(self, principal_id: str, machine_id: str):
        async with self.sessions() as session:
            jobs = (
                (
                    await session.execute(
                        text("""
                SELECT * FROM workflow_job WHERE principal_id=:p AND machine_id=:m
                ORDER BY created_at, job_id
            """),
                        {"p": principal_id, "m": machine_id},
                    )
                )
                .mappings()
                .all()
            )
            runs = (
                (
                    await session.execute(
                        text("""
                SELECT r.*, coalesce(s.cost,0)::text AS spend_usd,
                  coalesce(s.unpriced,0) AS unpriced_lines
                FROM workflow_run r JOIN workflow_job j USING(job_id)
                LEFT JOIN LATERAL (
                  SELECT sum(cost_usd) AS cost,
                    count(*) FILTER(WHERE cost_usd IS NULL) AS unpriced
                  FROM spend_event WHERE principal_id=j.principal_id AND thread_id=r.thread_id
                ) s ON true
                WHERE j.principal_id=:p AND j.machine_id=:m
                ORDER BY r.started_at, r.run_id
            """),
                        {"p": principal_id, "m": machine_id},
                    )
                )
                .mappings()
                .all()
            )
        return {"jobs": [dict(j) for j in jobs], "runs": [dict(r) for r in runs]}

    async def save(self, job_id: str, body: SaveJob):
        definition = body.definition.model_dump_json()
        next_run = (
            croniter(body.definition.cron, datetime.now(UTC)).get_next(datetime)
            if body.definition.cron and body.enabled
            else None
        )
        async with self.sessions() as session, session.begin():
            if body.expected_revision == 0:
                row = (
                    (
                        await session.execute(
                            text("""
                    INSERT INTO workflow_job
                    (job_id, principal_id, machine_id, definition, enabled, next_run_at,
                     trigger_cursor) VALUES (:id,:p,:m,CAST(:d AS jsonb),:e,:n,:c)
                    ON CONFLICT DO NOTHING RETURNING *
                """),
                            {
                                "id": job_id,
                                "p": body.principal_id,
                                "m": body.machine_id,
                                "d": definition,
                                "e": body.enabled,
                                "n": next_run,
                                "c": body.trigger_cursor,
                            },
                        )
                    )
                    .mappings()
                    .first()
                )
            else:
                row = (
                    (
                        await session.execute(
                            text("""
                    UPDATE workflow_job SET definition=CAST(:d AS jsonb), enabled=:e,
                      revision=revision+1, next_run_at=:n, trigger_cursor=:c
                    WHERE job_id=:id AND principal_id=:p AND machine_id=:m AND revision=:r
                    RETURNING *
                """),
                            {
                                "id": job_id,
                                "p": body.principal_id,
                                "m": body.machine_id,
                                "d": definition,
                                "e": body.enabled,
                                "n": next_run,
                                "c": body.trigger_cursor,
                                "r": body.expected_revision,
                            },
                        )
                    )
                    .mappings()
                    .first()
                )
            if row is None:
                raise HTTPException(409, "The workflow changed. Refresh before saving.")
            return dict(row)

    async def claim(self, job_id: str, body: ClaimJob):
        async with self.sessions() as session, session.begin():
            job = (
                (
                    await session.execute(
                        text("""
                SELECT * FROM workflow_job WHERE job_id=:id AND principal_id=:p
                  AND machine_id=:m FOR UPDATE
            """),
                        {"id": job_id, "p": body.principal_id, "m": body.machine_id},
                    )
                )
                .mappings()
                .first()
            )
            if job is None:
                raise HTTPException(404, "Workflow not found.")
            running = await session.scalar(
                text("SELECT count(*) FROM workflow_run WHERE job_id=:id AND state='running'"),
                {"id": job_id},
            )
            if running or job["revision"] != body.expected_revision:
                raise HTTPException(409, "Workflow is already running or changed. Refresh it.")
            definition = WorkflowDefinition.model_validate(job["definition"])
            now = datetime.now(UTC)
            if not body.manual:
                if not job["enabled"]:
                    raise HTTPException(409, "This schedule is paused.")
                if definition.cron:
                    if job["next_run_at"] is None or job["next_run_at"] > now:
                        raise HTTPException(409, "This schedule is not due.")
                    if body.trigger_key != job["next_run_at"].isoformat():
                        raise HTTPException(409, "The scheduled occurrence changed.")
                elif (
                    not definition.trigger
                    or body.trigger_cursor is None
                    or body.trigger_cursor == job["trigger_cursor"]
                ):
                    raise HTTPException(409, "No new trigger event.")
            row = (
                (
                    await session.execute(
                        text("""
                INSERT INTO workflow_run(run_id,job_id,thread_id,trigger_key,definition,state)
                VALUES (:r,:j,:t,:k,CAST(:d AS jsonb),'running')
                ON CONFLICT DO NOTHING RETURNING *
            """),
                        {
                            "r": body.run_id,
                            "j": job_id,
                            "t": body.thread_id,
                            "k": body.trigger_key,
                            "d": definition.model_dump_json(),
                        },
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise HTTPException(409, "This occurrence was already started.")
            if not body.manual:
                next_run = (
                    croniter(definition.cron, now).get_next(datetime) if definition.cron else None
                )
                await session.execute(
                    text("""
                    UPDATE workflow_job SET next_run_at=:n, trigger_cursor=:c WHERE job_id=:id
                """),
                    {"n": next_run, "c": body.trigger_cursor, "id": job_id},
                )
            return dict(row)

    async def finish(self, run_id: str, body: FinishJob):
        async with self.sessions() as session, session.begin():
            row = (
                (
                    await session.execute(
                        text("""
                UPDATE workflow_run r SET state=:s, verdict=:v, finished_at=now()
                FROM workflow_job j WHERE r.job_id=j.job_id AND r.run_id=:id
                  AND j.principal_id=:p AND j.machine_id=:m AND r.state='running'
                RETURNING r.*
            """),
                        {
                            "s": body.state,
                            "v": body.verdict,
                            "id": run_id,
                            "p": body.principal_id,
                            "m": body.machine_id,
                        },
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise HTTPException(409, "Run is absent or already finished.")
            return dict(row)


router = APIRouter(tags=["jobs"])


@router.get("/v1/jobs")
async def list_jobs(request: Request, principal_id: str, machine_id: str):
    return await request.app.state.job_service.list(principal_id, machine_id)


@router.put("/v1/jobs/{job_id}")
async def save_job(job_id: ULID, body: SaveJob, request: Request):
    return await request.app.state.job_service.save(job_id, body)


@router.post("/v1/jobs/{job_id}/runs")
async def claim_job(job_id: ULID, body: ClaimJob, request: Request):
    return await request.app.state.job_service.claim(job_id, body)


@router.post("/v1/job-runs/{run_id}/finish")
async def finish_job(run_id: ULID, body: FinishJob, request: Request):
    return await request.app.state.job_service.finish(run_id, body)
