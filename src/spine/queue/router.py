"""HTTP boundary for the M2H approval queue."""

from uuid import UUID

from fastapi import APIRouter, Query, Request

from spine.memory.service import MemoryValidationError
from spine.problems import ProblemJSONResponse, problem_openapi, problem_response
from spine.queue.contracts import (
    BatchDecisionResponse,
    ExtractionRequest,
    ExtractionResponse,
    QueueDecisionRequest,
    QueueDecisionResponse,
    QueueResponse,
    SeedRequest,
    SeedResponse,
)
from spine.queue.service import QueueConflictError, QueueNotFoundError, QueueValidationError

router = APIRouter(tags=["approval-queue"])
ERRORS = {
    401: problem_openapi("Bearer token missing or invalid"),
    422: problem_openapi("Invalid queue request"),
}


@router.post("/v1/extractions", response_model=ExtractionResponse, responses=ERRORS)
async def extract(
    body: ExtractionRequest, request: Request
) -> ExtractionResponse | ProblemJSONResponse:
    try:
        return await request.app.state.queue_service.extract(body)
    except (QueueValidationError, MemoryValidationError) as exc:
        return _problem(request, 422, str(exc))


@router.post(
    "/v1/seeds",
    response_model=SeedResponse,
    responses=ERRORS | {409: problem_openapi("Seed batch conflict")},
)
async def ingest_seed(body: SeedRequest, request: Request) -> SeedResponse | ProblemJSONResponse:
    try:
        return await request.app.state.queue_service.ingest_seed(body)
    except QueueConflictError as exc:
        return _problem(request, 409, str(exc))
    except QueueValidationError as exc:
        return _problem(request, 422, str(exc))


@router.get("/v1/approval-queue", response_model=QueueResponse, responses=ERRORS)
async def queue(
    request: Request,
    principal_id: str = Query(min_length=1),
    thread_id: UUID | None = None,
    birthplace: str | None = Query(default=None, pattern="^(thread|seed|symphony|curator)$"),
) -> QueueResponse:
    return await request.app.state.queue_service.list_pending(principal_id, thread_id, birthplace)


@router.post(
    "/v1/approval-queue/{item_uid}/decisions",
    response_model=QueueDecisionResponse,
    responses=ERRORS
    | {404: problem_openapi("Queue item missing"), 409: problem_openapi("Queue item conflict")},
)
async def decide(
    item_uid: str, body: QueueDecisionRequest, request: Request
) -> QueueDecisionResponse | ProblemJSONResponse:
    try:
        return await request.app.state.queue_service.decide(item_uid, body)
    except QueueNotFoundError:
        return _problem(request, 404, "Queue item does not exist.")
    except QueueConflictError as exc:
        return _problem(request, 409, str(exc))
    except QueueValidationError as exc:
        return _problem(request, 422, str(exc))


@router.post(
    "/v1/approval-queue/batches/{batch_uid}/decisions",
    response_model=BatchDecisionResponse,
    responses=ERRORS
    | {404: problem_openapi("Queue batch missing"), 409: problem_openapi("Queue batch conflict")},
)
async def decide_batch(
    batch_uid: UUID, body: QueueDecisionRequest, request: Request
) -> BatchDecisionResponse | ProblemJSONResponse:
    try:
        return await request.app.state.queue_service.decide_batch(batch_uid, body)
    except QueueNotFoundError:
        return _problem(request, 404, "Seed batch does not exist.")
    except QueueConflictError as exc:
        return _problem(request, 409, str(exc))
    except QueueValidationError as exc:
        return _problem(request, 422, str(exc))


def _problem(request: Request, status: int, detail: str) -> ProblemJSONResponse:
    return problem_response(
        status=status,
        title={404: "Not Found", 409: "Conflict"}.get(status, "Unprocessable Content"),
        detail=detail,
        instance=request.url.path,
        endpoint=f"{request.method} {request.url.path}",
    )
