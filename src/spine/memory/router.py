"""HTTP boundary for the SPEC C.4 memory endpoints."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from spine.contracts import (
    ContractModel,
    ContractRequest,
    CreatedMemoryResponse,
    CreateMemoryConflictResponse,
    DuplicateMemoryResponse,
    LabelConflictDetail,
    LabelConflictResponse,
    MemoryKind,
    MemoryListResponse,
    MemorySplitResponse,
    MemoryUnit,
    PatchMemoryConflictResponse,
    RevisionConflictResponse,
    SearchResponse,
    SimilarMemoryResponse,
)
from spine.embeddings import EmbeddingProviderError
from spine.memory.service import (
    CreateMemoryCommand,
    DuplicateMemoryError,
    EmptyPatchError,
    InvalidListQueryError,
    InvalidSearchQueryError,
    LabelConflictError,
    ListMemoriesQuery,
    MemoryCreated,
    MemoryNotFoundError,
    MemoryService,
    MemorySplitCreated,
    MemoryValidationError,
    PatchMemoryCommand,
    RevisionConflictError,
    SearchMemoriesQuery,
    SimilarMemories,
    SplitMemoryChild,
    SplitMemoryCommand,
)
from spine.problems import (
    ProblemJSONResponse,
    problem_openapi,
    problem_response,
)

router = APIRouter(tags=["memory"])
PublicMemoryStatus = Literal["active", "quarantined", "tombstoned"]

ERROR_RESPONSES = {
    401: problem_openapi("Bearer token missing or invalid"),
    422: problem_openapi("Request does not match the endpoint contract"),
    500: problem_openapi("Unexpected service failure"),
}

EMBEDDING_RESPONSES = ERROR_RESPONSES | {
    503: problem_openapi("Embedding provider unavailable"),
}


class CreateMemoryRequest(ContractRequest):
    principal_id: str
    label: str
    body: str
    kind: MemoryKind
    keywords: list[str] | None = None
    project_key: str | None = None
    thread_origin: str | None = None
    origin_thread_id: UUID | None = None
    origin_path: str | None = None
    origin_location: str | None = None
    editor: str
    machine_id: str
    force: bool = False


class SplitMemoryChildRequest(ContractRequest):
    label: str
    body: str
    keywords: Annotated[list[str], Field(min_length=2, max_length=5)]


class SplitMemoryRequest(ContractRequest):
    principal_id: str
    source_body: str
    children: Annotated[list[SplitMemoryChildRequest], Field(min_length=2, max_length=64)]
    thread_origin: str | None = None
    origin_thread_id: UUID | None = None
    origin_path: str | None = None
    origin_location: str | None = None
    editor: str
    machine_id: str


class PatchMemoryRequest(ContractRequest):
    expected_revision: int
    body: str | None = None
    label: str | None = None
    keywords: list[str] | None = None
    kind: MemoryKind | None = None
    origin_path: str | None = None
    pin: bool | None = None
    status: PublicMemoryStatus | None = None
    editor: str
    reason: str
    machine_id: str


class ThreadProject(ContractModel):
    thread_id: UUID
    project_key: str = Field(min_length=1)


class ProjectBackfillRequest(ContractRequest):
    principal_id: str = Field(min_length=1)
    threads: list[ThreadProject]


class ProjectBackfillResponse(ContractModel):
    updated: int


class SearchRequest(ContractRequest):
    principal_id: str
    query: str
    k: Annotated[int, Field(strict=True, ge=1, le=50)] = 10
    project_key: str | None = None


@router.post(
    "/v1/memories",
    status_code=201,
    response_model=CreatedMemoryResponse,
    responses=EMBEDDING_RESPONSES
    | {
        200: {
            "description": "Similar memories require an explicit force retry",
            "model": SimilarMemoryResponse,
        },
        409: {
            "description": "Active-label collision or hard duplicate",
            "model": CreateMemoryConflictResponse,
        },
    },
)
async def create_memory(
    body: CreateMemoryRequest,
    request: Request,
) -> CreatedMemoryResponse | JSONResponse:
    if body.editor == "maintenance":
        return _maintenance_write_refused(request)
    service = _memory_service(request)
    try:
        outcome = await service.create(
            CreateMemoryCommand(
                principal_id=body.principal_id,
                label=body.label,
                body=body.body,
                kind=body.kind,
                keywords=body.keywords or (),
                project_key=body.project_key,
                thread_origin=body.thread_origin,
                origin_thread_id=body.origin_thread_id,
                origin_path=body.origin_path,
                origin_location=body.origin_location,
                editor=body.editor,
                machine_id=body.machine_id,
                force=body.force,
            )
        )
    except LabelConflictError as error:
        return _label_conflict(error)
    except DuplicateMemoryError as error:
        conflict = DuplicateMemoryResponse(duplicate_of=error.duplicate_of)
        return JSONResponse(status_code=409, content=conflict.model_dump(mode="json"))
    except MemoryValidationError as error:
        return _unprocessable(request, str(error))
    except EmbeddingProviderError:
        return _provider_unavailable(request)

    if isinstance(outcome, SimilarMemories):
        response = SimilarMemoryResponse(created=None, similar=list(outcome.similar))
        return JSONResponse(status_code=200, content=response.model_dump(mode="json"))
    if not isinstance(outcome, MemoryCreated):  # pragma: no cover - closed service union
        raise TypeError(f"unexpected create outcome: {type(outcome).__name__}")
    return CreatedMemoryResponse(created=outcome.memory)


@router.post(
    "/v1/memory-splits",
    status_code=201,
    response_model=MemorySplitResponse,
    responses=EMBEDDING_RESPONSES
    | {
        200: {
            "description": "The first near-similar child requires an ordinary create decision",
            "model": SimilarMemoryResponse,
        },
        409: {
            "description": "The first child with an active-label collision or hard duplicate",
            "model": CreateMemoryConflictResponse,
        },
    },
)
async def split_memory(
    body: SplitMemoryRequest,
    request: Request,
) -> MemorySplitResponse | JSONResponse:
    if body.editor == "maintenance":
        return _maintenance_write_refused(request)
    try:
        outcome = await _memory_service(request).split(
            SplitMemoryCommand(
                principal_id=body.principal_id,
                source_body=body.source_body,
                children=tuple(
                    SplitMemoryChild(
                        label=child.label,
                        body=child.body,
                        keywords=tuple(child.keywords),
                    )
                    for child in body.children
                ),
                thread_origin=body.thread_origin,
                origin_thread_id=body.origin_thread_id,
                origin_path=body.origin_path,
                origin_location=body.origin_location,
                editor=body.editor,
                machine_id=body.machine_id,
            )
        )
    except LabelConflictError as error:
        return _label_conflict(error)
    except DuplicateMemoryError as error:
        conflict = DuplicateMemoryResponse(duplicate_of=error.duplicate_of)
        return JSONResponse(status_code=409, content=conflict.model_dump(mode="json"))
    except MemoryValidationError as error:
        return _unprocessable(request, str(error))
    except EmbeddingProviderError:
        return _provider_unavailable(request)

    if isinstance(outcome, SimilarMemories):
        response = SimilarMemoryResponse(created=None, similar=list(outcome.similar))
        return JSONResponse(status_code=200, content=response.model_dump(mode="json"))
    if not isinstance(outcome, MemorySplitCreated):  # pragma: no cover - closed service union
        raise TypeError(f"unexpected split outcome: {type(outcome).__name__}")
    return MemorySplitResponse(source=outcome.source, created=list(outcome.created))


@router.patch(
    "/v1/memories/{id}",
    response_model=MemoryUnit,
    responses=EMBEDDING_RESPONSES
    | {
        404: problem_openapi("Memory does not exist"),
        409: {
            "description": "CAS or active-label conflict",
            "model": PatchMemoryConflictResponse,
        },
    },
)
async def patch_memory(
    id: UUID,
    body: PatchMemoryRequest,
    request: Request,
) -> MemoryUnit | JSONResponse | ProblemJSONResponse:
    if body.editor == "maintenance":
        return _maintenance_write_refused(request)
    mutable = {
        field: getattr(body, field)
        for field in ("body", "label", "keywords", "kind", "origin_path", "pin", "status")
        if field in body.model_fields_set and getattr(body, field) is not None
    }
    try:
        return await _memory_service(request).patch(
            PatchMemoryCommand(
                memory_id=id,
                expected_revision=body.expected_revision,
                editor=body.editor,
                reason=body.reason,
                machine_id=body.machine_id,
                **mutable,
            )
        )
    except LabelConflictError as error:
        return _label_conflict(error)
    except RevisionConflictError as error:
        conflict = RevisionConflictResponse(conflict=error.current)
        return JSONResponse(status_code=409, content=conflict.model_dump(mode="json"))
    except MemoryNotFoundError:
        return problem_response(
            status=404,
            title="Not Found",
            detail=f"Memory {id} does not exist.",
            instance=request.url.path,
            endpoint=f"{request.method} {request.url.path}",
        )
    except EmptyPatchError:
        return _unprocessable(request, "PATCH requires at least one non-null mutable property.")
    except MemoryValidationError as error:
        return _unprocessable(request, str(error))
    except EmbeddingProviderError:
        return _provider_unavailable(request)


@router.get(
    "/v1/memories",
    response_model=MemoryListResponse,
    responses=ERROR_RESPONSES,
)
async def list_memories(
    request: Request,
    project_key: str | None = None,
    status: PublicMemoryStatus | None = None,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MemoryListResponse | ProblemJSONResponse:
    try:
        return await _memory_service(request).list(
            ListMemoriesQuery(
                project_key=project_key,
                status=status,
                q=q,
                limit=limit,
                offset=offset,
            )
        )
    except InvalidListQueryError as error:  # defensive for non-HTTP callers
        return problem_response(
            status=422,
            title="Unprocessable Content",
            detail=str(error),
            instance=request.url.path,
            endpoint=f"{request.method} {request.url.path}",
        )


@router.post(
    "/v1/memories/projects",
    response_model=ProjectBackfillResponse,
    responses=ERROR_RESPONSES,
)
async def backfill_memory_projects(
    body: ProjectBackfillRequest,
    request: Request,
) -> ProjectBackfillResponse:
    """F147: memories born in a thread with no recorded project take the thread's project."""
    updated = await _memory_service(request).backfill_projects(
        body.principal_id, {thread.thread_id: thread.project_key for thread in body.threads}
    )
    return ProjectBackfillResponse(updated=updated)


@router.post(
    "/v1/search",
    response_model=SearchResponse,
    responses=EMBEDDING_RESPONSES,
)
async def search(
    body: SearchRequest,
    request: Request,
) -> SearchResponse | ProblemJSONResponse:
    try:
        return await _memory_service(request).search(
            SearchMemoriesQuery(
                principal_id=body.principal_id,
                query=body.query,
                k=body.k,
                project_key=body.project_key,
            )
        )
    except InvalidSearchQueryError as error:  # defensive for non-HTTP callers
        return _unprocessable(request, str(error))
    except EmbeddingProviderError:
        return _provider_unavailable(request)


def _memory_service(request: Request) -> MemoryService:
    return request.app.state.memory_service


def _label_conflict(error: LabelConflictError) -> JSONResponse:
    conflict = LabelConflictResponse(
        label_conflict=LabelConflictDetail(memory_id=error.memory_id, label=error.label)
    )
    return JSONResponse(status_code=409, content=conflict.model_dump(mode="json"))


def _provider_unavailable(request: Request) -> ProblemJSONResponse:
    return problem_response(
        status=503,
        title="Service Unavailable",
        detail="The embedding provider could not complete the request.",
        instance=request.url.path,
        endpoint=f"{request.method} {request.url.path}",
    )


def _unprocessable(request: Request, detail: str) -> ProblemJSONResponse:
    return problem_response(
        status=422,
        title="Unprocessable Content",
        detail=detail,
        instance=request.url.path,
        endpoint=f"{request.method} {request.url.path}",
    )


def _maintenance_write_refused(request: Request) -> ProblemJSONResponse:
    return problem_response(
        status=422,
        title="Unprocessable Content",
        detail="Maintenance writes must pass through the bounded curator queue tools.",
        instance=request.url.path,
        endpoint=f"{request.method} {request.url.path}",
    )
