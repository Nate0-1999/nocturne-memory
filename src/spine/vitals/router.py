"""Bearer-protected live read boundary for A-028 Palace Vitals."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request

from spine.metrics_scope import metrics_principal
from spine.problems import ProblemJSONResponse, problem_openapi, problem_response
from spine.vitals.contracts import VitalsSnapshot
from spine.vitals.service import VitalsService

router = APIRouter(tags=["vitals"])


@router.get(
    "/v1/vitals",
    response_model=VitalsSnapshot,
    responses={
        401: problem_openapi("Bearer token missing or invalid"),
        403: problem_openapi("Only the Palace owner can view the whole Palace"),
        422: problem_openapi("Request does not match the endpoint contract"),
        500: problem_openapi("Unexpected service failure"),
    },
)
async def get_vitals(
    request: Request,
    principal_id: Annotated[str, Query(min_length=1)],
    scope: Literal["principal", "palace"] = "principal",
) -> VitalsSnapshot | ProblemJSONResponse:
    if set(request.query_params) - {"principal_id", "scope"}:
        return problem_response(
            status=422,
            title="Unprocessable Content",
            detail="Palace Vitals accepts only principal_id and scope.",
            instance=request.url.path,
            endpoint=f"{request.method} {request.url.path}",
        )
    return await _service(request).snapshot(
        principal_id=metrics_principal(request, principal_id, scope)
    )


@router.get(
    "/v1/vitals/threads/{thread_id}",
    response_model=VitalsSnapshot,
    responses={
        401: problem_openapi("Bearer token missing or invalid"),
        403: problem_openapi("Only the Palace owner can view the whole Palace"),
        422: problem_openapi("Request does not match the endpoint contract"),
        500: problem_openapi("Unexpected service failure"),
    },
)
async def get_thread_vitals(
    thread_id: UUID,
    request: Request,
    principal_id: Annotated[str, Query(min_length=1)],
    scope: Literal["principal", "palace"] = "principal",
) -> VitalsSnapshot | ProblemJSONResponse:
    if set(request.query_params) - {"principal_id", "scope"}:
        return problem_response(
            status=422,
            title="Unprocessable Content",
            detail="Thread Vitals accepts only principal_id and scope.",
            instance=request.url.path,
            endpoint=f"{request.method} {request.url.path}",
        )
    return await _service(request).snapshot(
        thread_id=thread_id, principal_id=metrics_principal(request, principal_id, scope)
    )


def _service(request: Request) -> VitalsService:
    return request.app.state.vitals_service


__all__ = ["router"]
