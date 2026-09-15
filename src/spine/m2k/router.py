"""Bearer-protected M2K visualization and scorer-control routes."""

from typing import Literal

from fastapi import APIRouter, Request

from spine.m2k.contracts import (
    ActivateScorerConfigRequest,
    CreateScorerConfigRequest,
    MemoryGraphQuery,
    MemoryGraphSnapshot,
    ScorerAuditionRequest,
    ScorerAuditionResponse,
    ScorerConfigurationView,
    ScorerConsoleQuery,
    ScorerConsoleSnapshot,
    ScorerSimulationRequest,
    ScorerSimulationResponse,
)
from spine.m2k.service import M2KService, M2KStateError
from spine.metrics_scope import metrics_principal
from spine.problems import ProblemJSONResponse, problem_openapi, problem_response

router = APIRouter(tags=["m2k"])

_RESPONSES = {
    401: problem_openapi("Bearer token missing or invalid"),
    409: problem_openapi("Scorer state changed or is not activatable"),
    422: problem_openapi("Request does not match the endpoint contract"),
    500: problem_openapi("Unexpected service failure"),
}


@router.post(
    "/v1/memory-graph/query",
    response_model=MemoryGraphSnapshot,
    responses=_RESPONSES,
)
async def memory_graph(
    body: MemoryGraphQuery,
    request: Request,
) -> MemoryGraphSnapshot:
    return await _service(request).memory_graph(body)


@router.post(
    "/v1/scorer-console/query",
    response_model=ScorerConsoleSnapshot,
    responses={**_RESPONSES, 403: problem_openapi("Whole-Palace status requires the owner")},
)
async def scorer_console(
    body: ScorerConsoleQuery,
    request: Request,
    scope: Literal["principal", "palace"] = "principal",
) -> ScorerConsoleSnapshot | ProblemJSONResponse:
    principal = metrics_principal(request, body.principal_id, scope)
    try:
        return await _service(request).scorer_console(body, palace_scope=principal is None)
    except M2KStateError as error:
        return _state_problem(request, error)


@router.post(
    "/v1/scorer-simulations",
    response_model=ScorerSimulationResponse,
    responses=_RESPONSES,
)
async def simulate_scorer(
    body: ScorerSimulationRequest,
    request: Request,
) -> ScorerSimulationResponse | ProblemJSONResponse:
    try:
        return await _service(request).simulate(body)
    except M2KStateError as error:
        return _state_problem(request, error)


@router.post(
    "/v1/scorer-auditions",
    response_model=ScorerAuditionResponse,
    responses=_RESPONSES,
)
async def audition_scorer(
    body: ScorerAuditionRequest,
    request: Request,
) -> ScorerAuditionResponse | ProblemJSONResponse:
    try:
        return await _service(request).audition(body)
    except M2KStateError as error:
        return _state_problem(request, error)


@router.post(
    "/v1/scorer-configs",
    response_model=ScorerConfigurationView,
    responses=_RESPONSES,
)
async def create_scorer_config(
    body: CreateScorerConfigRequest,
    request: Request,
) -> ScorerConfigurationView | ProblemJSONResponse:
    try:
        return await _service(request).create_scorer_config(body)
    except M2KStateError as error:
        return _state_problem(request, error)


@router.post(
    "/v1/scorer-configs/{version}/activate",
    response_model=ScorerConfigurationView,
    responses=_RESPONSES,
)
async def activate_scorer_config(
    version: str,
    body: ActivateScorerConfigRequest,
    request: Request,
) -> ScorerConfigurationView | ProblemJSONResponse:
    if not version.strip() or version != version.strip():
        return problem_response(
            status=422,
            title="Unprocessable Content",
            detail="Scorer version must be nonblank without surrounding whitespace.",
            instance=request.url.path,
            endpoint=f"{request.method} {request.url.path}",
        )
    try:
        return await _service(request).activate_proposal(version, body)
    except M2KStateError as error:
        return _state_problem(request, error)


def _service(request: Request) -> M2KService:
    return request.app.state.m2k_service


def _state_problem(request: Request, error: M2KStateError) -> ProblemJSONResponse:
    return problem_response(
        status=409,
        title="Conflict",
        detail=f"Scorer operation refused: {error.reason}.",
        instance=request.url.path,
        endpoint=f"{request.method} {request.url.path}",
    )


__all__ = ["router"]
