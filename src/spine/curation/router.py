"""Authenticated M3CU manual trigger and passive curator activity reads."""

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field

from spine.contracts import ContractModel
from spine.curation.contracts import (
    CuratorActivity,
    CuratorProgress,
    CuratorRunReceipt,
    CuratorRunRequest,
)
from spine.problems import ProblemJSONResponse, problem_openapi, problem_response

router = APIRouter(tags=["curation"])
ERRORS = {
    401: problem_openapi("Bearer token missing or invalid"),
    409: problem_openapi("A curator pass is already running"),
}


class CuratorPolicyWrite(ContractModel):
    principal_id: str = Field(min_length=1)
    policy: str = Field(min_length=1)


@router.get("/v1/curation/model-policy")
async def model_policy(request: Request, principal_id: str) -> dict[str, str]:
    if principal_id != request.app.state.settings.owner_principal_id:
        raise HTTPException(403, "Only the Palace owner can configure curator models.")
    return {"policy": await request.app.state.curator_policy.read(principal_id)}


@router.put("/v1/curation/model-policy")
async def set_model_policy(body: CuratorPolicyWrite, request: Request) -> dict[str, str]:
    if body.principal_id != request.app.state.settings.owner_principal_id:
        raise HTTPException(403, "Only the Palace owner can configure curator models.")
    try:
        policy = await request.app.state.curator_policy.save(body.principal_id, body.policy)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"policy": policy}


@router.get("/v1/curation", response_model=CuratorActivity, responses=ERRORS)
async def activity(
    request: Request,
    principal_id: str = Query(min_length=1),
) -> CuratorActivity:
    return await request.app.state.curator_service.activity(principal_id)


@router.get("/v1/curation/progress", response_model=CuratorProgress, responses=ERRORS)
async def progress(
    request: Request,
    principal_id: str = Query(min_length=1),
    after: int = Query(default=0, ge=0),
) -> CuratorProgress:
    return await request.app.state.curator_service.progress(principal_id, after)


@router.post(
    "/v1/curation/runs",
    response_model=CuratorRunReceipt,
    responses=ERRORS,
)
async def run(
    body: CuratorRunRequest,
    request: Request,
) -> CuratorRunReceipt | ProblemJSONResponse:
    receipt = await request.app.state.curator_service.run(
        body.principal_id,
        machine_id=body.machine_id,
        trigger="manual",
    )
    if receipt is None:
        return problem_response(
            status=409,
            title="Conflict",
            detail="A curator pass is already running for this Palace.",
            instance=request.url.path,
            endpoint=f"{request.method} {request.url.path}",
        )
    return receipt


__all__ = ["router"]
