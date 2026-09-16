"""Authenticated M3CU manual trigger and passive curator activity reads."""

from fastapi import APIRouter, Query, Request

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
