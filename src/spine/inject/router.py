"""HTTP boundary for the SPEC C.4 injection and feedback endpoints."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import Field

from spine.contracts import (
    CommitResponse,
    ContractRequest,
    FeedbackResponse,
    InjectionEventAnnotationsRequest,
    InjectionEventAnnotationsResponse,
    PrepareResponse,
    RestoredInjection,
)
from spine.embeddings import EmbeddingProviderError
from spine.inject.annotations import (
    AnnotationConflictError,
    AnnotationFingerprintMismatchError,
    AnnotationTargetNotFoundError,
    InjectionEventAnnotationService,
)
from spine.inject.decisions import (
    CommitCommand,
    DecisionService,
    FeedbackCommand,
    InjectionNotFoundError,
    InvalidCommitChoicesError,
    OutcomeConflictError,
    RemovedDecision,
)
from spine.inject.service import (
    InvalidRescoreStateError,
    PrepareCommand,
    PrepareConflictError,
    PrepareService,
    ThreadAlreadyPreparedError,
    ThreadIdentityConflictError,
)
from spine.learner.worker import LearnerWorker
from spine.problems import (
    ProblemJSONResponse,
    problem_openapi,
    problem_response,
)

router = APIRouter(tags=["injection"])

ERROR_RESPONSES = {
    401: problem_openapi("Bearer token missing or invalid"),
    422: problem_openapi("Request does not match the endpoint contract"),
    500: problem_openapi("Unexpected service failure"),
}

PREPARE_RESPONSES = ERROR_RESPONSES | {
    409: problem_openapi("Thread identity or snapshot conflict"),
    503: problem_openapi("Embedding provider unavailable"),
}

DECISION_RESPONSES = ERROR_RESPONSES | {
    404: problem_openapi("Injection event membership does not exist"),
    409: problem_openapi("Injection outcome conflicts with the request"),
}

ANNOTATION_RESPONSES = ERROR_RESPONSES | {
    404: problem_openapi("Injection event does not exist"),
    409: problem_openapi("Target fingerprint or immutable annotation conflicts"),
}


class PrepareRequest(ContractRequest):
    thread_id: UUID
    agent_id: str
    machine_id: str
    principal_id: str
    project_key: str | None = None
    location_path: str | None = None
    current_location: str | None = None
    agent_kind: str | None = None
    prompt: str
    model_context_tokens: Annotated[int, Field(gt=0)]
    mode: Literal["gate", "autonomous"] = "gate"
    current_memory_ids: list[UUID] = Field(default_factory=list)
    confirmed_memory_ids: list[UUID] = Field(default_factory=list)
    excluded_memory_ids: list[UUID] = Field(default_factory=list)


class RemovedMemory(ContractRequest):
    memory_id: UUID
    reason: Literal["not_relevant", "wrong", "never"]


@router.get("/v1/inject/threads/{thread_id}", response_model=RestoredInjection | None)
async def restore_injection(
    thread_id: UUID,
    principal_id: str,
    request: Request,
) -> RestoredInjection | None:
    return await _decision_service(request).restore(thread_id, principal_id)


class MemoryScoresRequest(PrepareRequest):
    memory_ids: list[UUID]


@router.post("/v1/memories/scores", response_model=dict[str, float], responses=PREPARE_RESPONSES)
async def memory_scores(
    body: MemoryScoresRequest,
    request: Request,
) -> dict[str, float] | ProblemJSONResponse:
    """M3MP: current-context scores are observations, never gate dispositions."""
    try:
        return await _prepare_service(request).preview_scores(
            PrepareCommand(
                **{
                    **body.model_dump(exclude={"memory_ids"}),
                    "agent_kind": body.agent_kind or "general",
                }
            ),
            body.memory_ids,
        )
    except EmbeddingProviderError:
        return problem_response(
            status=503,
            title="Service Unavailable",
            detail="Memory scoring unavailable",
            instance=request.url.path,
            endpoint="POST /v1/memories/scores",
        )


class CommitRequest(ContractRequest):
    injection_id: UUID
    removed: list[RemovedMemory]
    added_back: list[UUID]


class FeedbackRequest(ContractRequest):
    injection_id: UUID
    memory_id: UUID
    signal: Literal["mid_thread_removed", "mid_thread_added", "cited"]


@router.post(
    "/v1/injection-event-annotations",
    response_model=InjectionEventAnnotationsResponse,
    responses=ANNOTATION_RESPONSES,
)
async def annotate_injection_events(
    body: InjectionEventAnnotationsRequest,
    request: Request,
) -> InjectionEventAnnotationsResponse | ProblemJSONResponse:
    try:
        accepted = await _annotation_service(request).append(body.annotations)
    except AnnotationTargetNotFoundError as error:
        return _decision_problem(request, 404, "Not Found", str(error))
    except (AnnotationFingerprintMismatchError, AnnotationConflictError) as error:
        return _decision_problem(request, 409, "Conflict", str(error))
    return InjectionEventAnnotationsResponse(accepted=accepted)


@router.post(
    "/v1/inject/prepare",
    response_model=PrepareResponse,
    responses=PREPARE_RESPONSES,
)
async def prepare(
    body: PrepareRequest,
    request: Request,
) -> PrepareResponse | ProblemJSONResponse:
    try:
        result = await _prepare_service(request).prepare(
            PrepareCommand(
                thread_id=body.thread_id,
                agent_id=body.agent_id,
                machine_id=body.machine_id,
                principal_id=body.principal_id,
                project_key=body.project_key,
                location_path=body.location_path,
                current_location=body.current_location,
                agent_kind=body.agent_kind if body.agent_kind is not None else "general",
                prompt=body.prompt,
                model_context_tokens=body.model_context_tokens,
                mode=body.mode,
                current_memory_ids=tuple(body.current_memory_ids),
                confirmed_memory_ids=tuple(body.confirmed_memory_ids),
                excluded_memory_ids=tuple(body.excluded_memory_ids),
            )
        )
        return result
    except ThreadAlreadyPreparedError:
        return _conflict(
            request,
            "This thread already has its single M1 memory injection.",
        )
    except ThreadIdentityConflictError:
        return _conflict(
            request,
            "The thread ID is already assigned to different request metadata.",
        )
    except InvalidRescoreStateError as error:
        return _conflict(request, str(error))
    except PrepareConflictError:
        return _conflict(
            request,
            "Concurrent memory changes prevented a stable prepare snapshot.",
        )
    except EmbeddingProviderError:
        return problem_response(
            status=503,
            title="Service Unavailable",
            detail="The embedding provider could not complete the request.",
            instance=request.url.path,
            endpoint=f"{request.method} {request.url.path}",
        )


@router.post(
    "/v1/inject/commit",
    response_model=CommitResponse,
    responses=DECISION_RESPONSES,
)
async def commit(
    body: CommitRequest,
    request: Request,
) -> CommitResponse | ProblemJSONResponse:
    try:
        result = await _decision_service(request).commit(
            CommitCommand(
                injection_id=body.injection_id,
                removed=tuple(
                    RemovedDecision(memory_id=item.memory_id, reason=item.reason)
                    for item in body.removed
                ),
                added_back=tuple(body.added_back),
            )
        )
        return result
    except InjectionNotFoundError as error:
        return _decision_problem(request, 404, "Not Found", str(error))
    except InvalidCommitChoicesError as error:
        return _decision_problem(request, 422, "Unprocessable Content", str(error))
    except OutcomeConflictError as error:
        return _decision_problem(request, 409, "Conflict", str(error))


@router.post(
    "/v1/feedback",
    response_model=FeedbackResponse,
    responses=DECISION_RESPONSES,
)
async def feedback(
    body: FeedbackRequest,
    request: Request,
) -> FeedbackResponse | ProblemJSONResponse:
    try:
        result = await _decision_service(request).feedback(
            FeedbackCommand(
                injection_id=body.injection_id,
                memory_id=body.memory_id,
                signal=body.signal,
            )
        )
        return result
    except InjectionNotFoundError as error:
        return _decision_problem(request, 404, "Not Found", str(error))
    except OutcomeConflictError as error:
        return _decision_problem(request, 409, "Conflict", str(error))


def _prepare_service(request: Request) -> PrepareService:
    return request.app.state.prepare_service


def _decision_service(request: Request) -> DecisionService:
    return request.app.state.decision_service


def _annotation_service(request: Request) -> InjectionEventAnnotationService:
    return request.app.state.injection_event_annotation_service


def _learner_worker(request: Request) -> LearnerWorker:
    return request.app.state.learner_worker


def _conflict(request: Request, detail: str) -> ProblemJSONResponse:
    return problem_response(
        status=409,
        title="Conflict",
        detail=detail,
        instance=request.url.path,
        endpoint=f"{request.method} {request.url.path}",
    )


def _decision_problem(
    request: Request,
    status: int,
    title: str,
    detail: str,
) -> ProblemJSONResponse:
    return problem_response(
        status=status,
        title=title,
        detail=detail,
        instance=request.url.path,
        endpoint=f"{request.method} {request.url.path}",
    )
