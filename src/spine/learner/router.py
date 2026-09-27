"""Authenticated manual trigger for M2F batch proposals."""

from fastapi import APIRouter, HTTPException, Request, Response

from spine.learner.contracts import CompactionTrigger, RetrainResponse
from spine.learner.service import LearnerService, OptimizationTrigger
from spine.problems import problem_openapi

router = APIRouter(tags=["learner"])


@router.post(
    "/retrain",
    response_model=RetrainResponse,
    responses={
        401: problem_openapi("Bearer token missing or invalid"),
        403: problem_openapi("Only the Palace owner can retrain the scorer"),
        500: problem_openapi("Training evidence is invalid or retraining failed"),
    },
)
async def retrain(request: Request, principal_id: str | None = None) -> RetrainResponse:
    _require_owner(request, principal_id)
    return await _service(request).retrain()


@router.post("/v1/compactions", status_code=202)
async def compaction(
    request: Request, body: CompactionTrigger, principal_id: str | None = None
) -> Response:
    """Queue a main-thread entropy event without awaiting the optimizer."""
    _require_owner(request, principal_id)
    request.app.state.learner_worker.notify(
        OptimizationTrigger(event_uid=body.event_uid, thread_id=body.thread_id)
    )
    return Response(status_code=202)


def _require_owner(request: Request, principal_id: str | None) -> None:
    if principal_id != request.app.state.settings.owner_principal_id:
        raise HTTPException(403, "Only the Palace owner can retrain the scorer.")


def _service(request: Request) -> LearnerService:
    return request.app.state.learner_service


__all__ = ["router"]
