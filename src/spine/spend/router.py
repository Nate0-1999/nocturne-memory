"""Authenticated HTTP boundary for receipt ingestion and M3SP's table read."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from spine.metrics_scope import metrics_principal
from spine.problems import ProblemJSONResponse, problem_openapi, problem_response
from spine.spend.contracts import (
    InfrastructureInvoice,
    SpendEventsRequest,
    SpendEventsResponse,
    SpendTableSnapshot,
)
from spine.spend.service import SpendEventConflictError, SpendService

router = APIRouter(prefix="/v1/spend", tags=["spend"])


@router.post(
    "/invoices",
    response_model=SpendEventsResponse,
    responses={
        401: problem_openapi("Bearer token missing or invalid"),
        403: problem_openapi("Only the Palace owner can record invoices"),
        409: problem_openapi("Invoice ID conflicts with its append-only receipt"),
        422: problem_openapi("Request does not match the endpoint contract"),
    },
)
async def record_invoice(
    body: InfrastructureInvoice,
    request: Request,
    principal_id: Annotated[str, Query(min_length=1)],
) -> SpendEventsResponse | ProblemJSONResponse:
    """M3SR owner ruling: one measured infra line per cloud invoice."""
    if principal_id != request.app.state.settings.owner_principal_id:
        raise HTTPException(403, "Only the Palace owner can record infrastructure invoices.")
    try:
        accepted = await _service(request).invoice(principal_id, body)
    except SpendEventConflictError:
        return problem_response(
            status=409,
            title="Conflict",
            detail=(
                "This invoice ID already has a different amount or date; the ledger is append-only."
            ),
            instance=request.url.path,
            endpoint=f"{request.method} {request.url.path}",
        )
    return SpendEventsResponse(accepted=accepted)


@router.get(
    "/table",
    response_model=SpendTableSnapshot,
    responses={
        401: problem_openapi("Bearer token missing or invalid"),
        403: problem_openapi("Only the Palace owner can view the whole Palace"),
        422: problem_openapi("Request does not match the endpoint contract"),
        500: problem_openapi("Unexpected service failure"),
    },
)
async def read_spend_table(
    request: Request,
    principal_id: Annotated[str, Query(min_length=1)],
    thread_id: Annotated[list[UUID] | None, Query()] = None,
    scope: Literal["principal", "palace", "global", "threads"] = "principal",
) -> SpendTableSnapshot:
    """Filter before aggregation; thread selectors never widen principal scope."""

    scoped_threads = thread_id if thread_id is not None else ([] if scope == "threads" else None)
    principal = metrics_principal(
        request, principal_id, "palace" if scope == "palace" else "principal"
    )
    snapshot = await _service(request).table(scoped_threads, principal_id=principal)
    return snapshot.model_copy(
        update={
            "can_record_invoice": (principal_id == request.app.state.settings.owner_principal_id)
        }
    )


@router.post(
    "/events",
    response_model=SpendEventsResponse,
    responses={
        401: problem_openapi("Bearer token missing or invalid"),
        409: problem_openapi("event_uid conflicts with an existing receipt"),
        422: problem_openapi("Request does not match the endpoint contract"),
        500: problem_openapi("Unexpected service failure"),
    },
)
async def append_spend_events(
    body: SpendEventsRequest,
    request: Request,
) -> SpendEventsResponse | ProblemJSONResponse:
    try:
        accepted = await _service(request).append(body.events)
    except SpendEventConflictError as error:
        return problem_response(
            status=409,
            title="Conflict",
            detail=f"Spend event {error.event_uid} conflicts with its append-only receipt.",
            instance=request.url.path,
            endpoint=f"{request.method} {request.url.path}",
        )
    return SpendEventsResponse(accepted=accepted)


def _service(request: Request) -> SpendService:
    return request.app.state.spend_service


__all__ = ["router"]
