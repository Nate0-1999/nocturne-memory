"""Health, auth, validation, and committed API contract tests."""

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from conftest import TOKEN
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from spine import __version__
from spine.api_contract import (
    API_CONTRACT_VERSION,
    ApiContractDriftError,
    require_known_contract_fingerprint,
)

ROOT = Path(__file__).resolve().parents[1]
MEMORY_ID = "00000000-0000-0000-0000-000000000001"
INJECTION_ID = "00000000-0000-0000-0000-000000000002"

SPINE_ROUTES = {
    ("GET", "/v1/identity"),
    ("POST", "/v1/compactions"),
    ("POST", "/v1/inject/prepare"),
    ("POST", "/v1/inject/commit"),
    ("POST", "/v1/feedback"),
    ("POST", "/v1/injection-event-annotations"),
    ("POST", "/v1/memories"),
    ("POST", "/v1/memories/scores"),
    ("POST", "/v1/memory-splits"),
    ("PATCH", "/v1/memories/{id}"),
    ("GET", "/v1/memories"),
    ("POST", "/v1/search"),
    ("POST", "/v1/spend/events"),
    ("POST", "/v1/spend/invoices"),
    ("GET", "/v1/spend/table"),
    ("GET", "/v1/vitals"),
    ("GET", "/v1/vitals/threads/{thread_id}"),
    ("POST", "/v1/memory-graph/query"),
    ("POST", "/v1/scorer-console/query"),
    ("POST", "/v1/scorer-simulations"),
    ("POST", "/v1/scorer-auditions"),
    ("POST", "/v1/scorer-configs"),
    ("POST", "/v1/scorer-configs/{version}/activate"),
    ("POST", "/v1/extractions"),
    ("POST", "/v1/seeds"),
    ("GET", "/v1/approval-queue"),
    ("POST", "/v1/approval-queue/{item_uid}/decisions"),
    ("POST", "/v1/approval-queue/batches/{batch_uid}/decisions"),
    ("GET", "/v1/curation"),
    ("GET", "/v1/curation/progress"),
    ("POST", "/v1/curation/runs"),
    ("POST", "/v1/transcripts"),
    ("GET", "/v1/transcripts"),
    ("GET", "/v1/transcripts/status"),
    ("GET", "/v1/jobs"),
    ("PUT", "/v1/jobs/{job_id}"),
    ("POST", "/v1/jobs/{job_id}/runs"),
    ("POST", "/v1/job-runs/{run_id}/finish"),
    ("POST", "/v1/symphony/memories"),
    ("POST", "/v1/symphony/memories/query"),
    ("POST", "/v1/symphony/runs/{run_id}/resolve"),
}


def _assert_problem(response: Any, *, status: int, endpoint: str) -> None:
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["type"] == "about:blank"
    assert body["title"]
    assert body["status"] == status
    assert body["detail"]
    assert body["instance"]
    assert body["endpoint"] == endpoint


async def test_health_endpoints_and_auth_are_live(app: FastAPI) -> None:
    """A-056 and SPEC B.6 rule 12 keep authenticated product, API-contract, and storage-schema
    versions separate so clients never infer compatibility from internals.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unauthorized_healthz = await client.get("/healthz")
        unauthorized_health = await client.get("/health")
        healthy_healthz = await client.get("/healthz", headers={"Authorization": f"Bearer {TOKEN}"})
        healthy_health = await client.get("/health", headers={"Authorization": f"Bearer {TOKEN}"})

    _assert_problem(unauthorized_healthz, status=401, endpoint="GET /healthz")
    _assert_problem(unauthorized_health, status=401, endpoint="GET /health")
    assert unauthorized_healthz.headers["www-authenticate"] == "Bearer"
    assert unauthorized_health.headers["www-authenticate"] == "Bearer"
    assert healthy_healthz.status_code == 200
    assert healthy_health.status_code == 200
    assert healthy_healthz.json() == {
        "ok": True,
        "version": __version__,
        "api_contract_version": "0.1.24",
        "schema_version": "0025",
    }
    assert healthy_healthz.json()["api_contract_version"] == API_CONTRACT_VERSION
    assert re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", API_CONTRACT_VERSION)
    assert healthy_health.json() == healthy_healthz.json()
    assert "/health" not in app.openapi()["paths"]


async def test_schema_head_changes_do_not_change_the_api_contract_version(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A-056 and SPEC B.6 rule 12 prevent a schema-only Spine change from demanding client
    action when its endpoints, payloads, and semantics are unchanged.
    """
    monkeypatch.setattr("spine.main.packaged_head", lambda: "9999")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/healthz", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200
    assert response.json()["schema_version"] == "9999"
    assert response.json()["api_contract_version"] == API_CONTRACT_VERSION


def test_contract_fingerprint_rejects_unversioned_openapi_drift(app: FastAPI) -> None:
    """A-056 and SPEC B.6 rule 12 make silent client-contract drift fail mechanically while
    ordinary product-release metadata remains independently versioned.
    """
    fingerprints = json.loads((ROOT / "api_contract_fingerprints.json").read_text(encoding="utf-8"))
    openapi = app.openapi()
    require_known_contract_fingerprint(openapi, fingerprints)

    release_only = deepcopy(openapi)
    release_only["info"]["version"] = "999.999.999"
    require_known_contract_fingerprint(release_only, fingerprints)

    contract_drift = deepcopy(openapi)
    contract_drift["paths"]["/v1/unversioned-contract-drift"] = {}
    with pytest.raises(ApiContractDriftError, match="bump API_CONTRACT_VERSION"):
        require_known_contract_fingerprint(contract_drift, fingerprints)

    with pytest.raises(ApiContractDriftError, match="has no recorded OpenAPI fingerprint"):
        require_known_contract_fingerprint(openapi, fingerprints, version="999.0.0")


async def test_retrain_is_bearer_protected_before_any_training_work(app: FastAPI) -> None:
    """SPEC C.4 is defended by verifying that retrain is bearer protected before any training
    work; this prevents drift in the public API and error contract.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/retrain")

    _assert_problem(response, status=401, endpoint="POST /retrain")


async def test_validation_errors_are_rfc7807(app: FastAPI) -> None:
    """SPEC C.4 is defended by verifying that validation errors are rfc7807; this prevents
    drift in the public API and error contract.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/memories",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={},
        )

    _assert_problem(response, status=422, endpoint="POST /v1/memories")
    assert response.json()["errors"]


async def test_exact_c4_bodies_reject_extra_fields(app: FastAPI) -> None:
    """SPEC C.4 is defended by verifying that exact c4 bodies reject extra fields; this
    prevents drift in the public API and error contract.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/search",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"principal_id": "owner", "query": "editor", "invented": True},
        )

    _assert_problem(response, status=422, endpoint="POST /v1/search")
    assert response.json()["errors"][0]["type"] == "extra_forbidden"


async def test_v15_machine_id_is_required_on_create_and_patch(app: FastAPI) -> None:
    """SPEC C.4 is defended by verifying that v15 machine id is required on create and patch;
    this prevents drift in the public API and error contract.
    """
    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create = await client.post(
            "/v1/memories",
            headers=headers,
            json={
                "principal_id": "owner",
                "label": "Editor preference",
                "body": "The owner prefers tabs.",
                "kind": "preference",
                "editor": "user",
            },
        )
        patch = await client.patch(
            f"/v1/memories/{MEMORY_ID}",
            headers=headers,
            json={"expected_revision": 1, "editor": "user", "reason": "fix"},
        )

    _assert_problem(create, status=422, endpoint="POST /v1/memories")
    _assert_problem(patch, status=422, endpoint=f"PATCH /v1/memories/{MEMORY_ID}")
    assert {error["loc"][-1] for error in create.json()["errors"]} == {"machine_id"}
    assert {error["loc"][-1] for error in patch.json()["errors"]} == {"machine_id"}


async def test_v15_list_paging_contract_is_validated(app: FastAPI) -> None:
    """SPEC C.4 is defended by verifying that v15 list paging contract is validated; this
    prevents drift in the public API and error contract.
    """
    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        excessive = await client.get("/v1/memories?limit=201", headers=headers)
        empty = await client.get("/v1/memories?limit=0", headers=headers)
        negative = await client.get("/v1/memories?offset=-1", headers=headers)

    _assert_problem(excessive, status=422, endpoint="GET /v1/memories")
    _assert_problem(empty, status=422, endpoint="GET /v1/memories")
    _assert_problem(negative, status=422, endpoint="GET /v1/memories")


async def test_http_errors_are_rfc7807(app: FastAPI) -> None:
    """SPEC C.4 is defended by verifying that http errors are rfc7807; this prevents drift in
    the public API and error contract.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/not-a-route",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

    _assert_problem(response, status=404, endpoint="GET /not-a-route")


async def test_unexpected_service_errors_are_sanitized_rfc7807(app: FastAPI) -> None:
    """SPEC C.4 is defended by verifying that unexpected service errors are sanitized rfc7807;
    this prevents drift in the public API and error contract.
    """
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/memories",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

    _assert_problem(response, status=500, endpoint="GET /v1/memories")
    assert response.json()["detail"] == "The request could not be completed."


def test_exactly_the_lawful_spine_routes_are_registered(app: FastAPI) -> None:
    """SPEC C.4 and A-047 are defended by verifying that exactly the lawful Spine routes
    are registered; this prevents drift in the public API and simulation boundary.
    """
    actual = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/v1/")
        for method in operations
    }
    assert actual == SPINE_ROUTES


def test_committed_openapi_is_current(app: FastAPI) -> None:
    """A-044 keeps the committed public Vitals schema literal and current."""
    committed = json.loads((ROOT / "openapi.json").read_text(encoding="utf-8"))
    assert committed == app.openapi()
    assert committed["components"]["securitySchemes"]["StaticBearer"] == {
        "scheme": "bearer",
        "type": "http",
    }
    assert committed["components"]["schemas"]["SearchRequest"]["additionalProperties"] is False
    assert committed["components"]["schemas"]["SpendEventsRequest"]["additionalProperties"] is False
    assert "requestBody" not in committed["paths"]["/retrain"]["post"]
    assert committed["paths"]["/retrain"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/RetrainResponse"}

    create_operation = committed["paths"]["/v1/memories"]["post"]
    create_request = committed["components"]["schemas"]["CreateMemoryRequest"]
    assert {"machine_id", "editor"} <= set(create_request["required"])
    assert create_request["properties"]["force"]["default"] is False
    assert "origin_path" not in create_request["required"]
    assert {
        option.get("type") for option in create_request["properties"]["origin_path"]["anyOf"]
    } == {
        "string",
        "null",
    }
    assert {"200", "201", "409"} <= set(create_operation["responses"])
    assert "501" not in create_operation["responses"]
    assert create_operation["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CreatedMemoryResponse"
    }
    assert create_operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SimilarMemoryResponse"
    }
    assert create_operation["responses"]["409"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CreateMemoryConflictResponse"
    }

    patch_request = committed["components"]["schemas"]["PatchMemoryRequest"]
    assert "machine_id" in patch_request["required"]
    assert "origin_path" not in patch_request["required"]
    assert {
        option.get("type") for option in patch_request["properties"]["origin_path"]["anyOf"]
    } == {
        "string",
        "null",
    }
    patch_operation = committed["paths"]["/v1/memories/{id}"]["patch"]
    assert "501" not in patch_operation["responses"]
    assert patch_operation["responses"]["409"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/PatchMemoryConflictResponse"
    }
    assert "501" not in committed["paths"]["/v1/memories"]["get"]["responses"]
    prepare_operation = committed["paths"]["/v1/inject/prepare"]["post"]
    assert "501" not in prepare_operation["responses"]
    assert {"200", "409", "503"} <= set(prepare_operation["responses"])
    prepare_request = committed["components"]["schemas"]["PrepareRequest"]
    assert prepare_request["properties"]["model_context_tokens"]["exclusiveMinimum"] == 0
    commit_response = committed["paths"]["/v1/inject/commit"]["post"]
    commit_schema = commit_response["responses"]["200"]["content"]["application/json"]["schema"]
    assert commit_schema == {"$ref": "#/components/schemas/CommitResponse"}
    assert "wrong_removed" in committed["components"]["schemas"]["CommitResponse"]["required"]
    assert "501" not in commit_response["responses"]
    assert {"200", "404", "409", "422"} <= set(commit_response["responses"])
    feedback_response = committed["paths"]["/v1/feedback"]["post"]
    assert "501" not in feedback_response["responses"]
    assert {"200", "404", "409", "422"} <= set(feedback_response["responses"])
    annotation_operation = committed["paths"]["/v1/injection-event-annotations"]["post"]
    assert {"200", "401", "404", "409", "422", "500"} <= set(annotation_operation["responses"])
    annotation_request = committed["components"]["schemas"]["InjectionEventAnnotationsRequest"]
    assert annotation_request["additionalProperties"] is False
    assert annotation_request["properties"]["annotations"]["minItems"] == 1
    assert annotation_request["properties"]["annotations"]["maxItems"] == 100
    search_operation = committed["paths"]["/v1/search"]["post"]
    assert "501" not in search_operation["responses"]
    assert {"200", "401", "422", "500", "503"} <= set(search_operation["responses"])
    search_k = committed["components"]["schemas"]["SearchRequest"]["properties"]["k"]
    assert search_k == {
        "default": 10,
        "maximum": 50,
        "minimum": 1,
        "title": "K",
        "type": "integer",
    }
    spend_operation = committed["paths"]["/v1/spend/events"]["post"]
    assert {"200", "401", "409", "422", "500"} <= set(spend_operation["responses"])
    spend_event = committed["components"]["schemas"]["SpendEventInput"]
    assert {"event_uid", "product_type", "quantity_type", "purpose", "ref"} <= set(
        spend_event["required"]
    )
    vitals_operation = committed["paths"]["/v1/vitals"]["get"]
    assert {item["name"] for item in vitals_operation["parameters"]} == {"principal_id", "scope"}
    assert {"200", "401", "422", "500"} <= set(vitals_operation["responses"])
    vitals_schema = committed["components"]["schemas"]["VitalsSnapshot"]
    assert set(vitals_schema["required"]) == {
        "as_of",
        "window_minutes",
        "spend",
        "reconciliation",
        "lifecycle_rates",
        "palace_counts",
        "resources",
    }
    assert vitals_schema["properties"]["window_minutes"]["const"] == 60
    spend_point = committed["components"]["schemas"]["SpendPoint"]
    cost_schema = spend_point["properties"]["cost_usd"]["anyOf"]
    assert cost_schema == [
        {"$ref": "#/components/schemas/DecimalString"},
        {"type": "null"},
    ]
    assert committed["components"]["schemas"]["DecimalString"] == {
        "pattern": r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$",
        "type": "string",
    }
    lifecycle_metric = committed["components"]["schemas"]["LifecycleMetric"]
    assert set(lifecycle_metric["enum"]) == {
        "created",
        "reinforced",
        "superseded",
        "merged",
        "quarantined",
        "tombstoned",
        "add_backs",
    }
    assert set(committed["components"]["schemas"]["PalaceMetric"]["enum"]) == {
        "active_units",
        "pinned_units",
        "candidates_pending",
        "edges",
        "staged_units",
        "queue_depth",
    }

    memory_unit_fields = {
        "memory_id",
        "principal_id",
        "label",
        "body",
        "kind",
        "keywords",
        "project_key",
        "thread_origin",
        "origin_thread_id",
        "origin_path",
        "pin",
        "status",
        "revision",
        "stats",
        "bias",
        "embedding_model",
        "created_at",
        "updated_at",
    }
    assert set(committed["components"]["schemas"]["MemoryUnit"]["required"]) == memory_unit_fields
    assert {
        option.get("type")
        for option in committed["components"]["schemas"]["MemoryUnit"]["properties"]["origin_path"][
            "anyOf"
        ]
    } == {
        "string",
        "null",
    }
    assert "origin_path" not in committed["components"]["schemas"]["MemoryFeatures"]["properties"]
    assert "thread" in committed["components"]["schemas"]["MemoryFeatures"]["properties"]
    assert set(committed["components"]["schemas"]["ScoredMemoryCard"]["required"]) == {
        "memory_id",
        "label",
        "body",
        "kind",
        "pin",
        "score",
        "features",
        "rank",
    }
    scored_properties = committed["components"]["schemas"]["ScoredMemoryCard"]["properties"]
    assert scored_properties["features"] == {"$ref": "#/components/schemas/MemoryFeatures"}
    assert scored_properties["rank"]["type"] == "integer"

    similarity_properties = committed["components"]["schemas"]["SimilarityMemoryCard"]["properties"]
    assert similarity_properties["features"]["type"] == "null"
    assert similarity_properties["rank"]["type"] == "null"
    prepare_items = committed["components"]["schemas"]["PrepareResponse"]["properties"]["injected"][
        "items"
    ]
    duplicate_card = committed["components"]["schemas"]["DuplicateMemoryResponse"]["properties"][
        "duplicate_of"
    ]
    similar_items = committed["components"]["schemas"]["SimilarMemoryResponse"]["properties"][
        "similar"
    ]["items"]
    search_items = committed["components"]["schemas"]["SearchResponse"]["properties"]["results"][
        "items"
    ]
    assert similar_items == {"$ref": "#/components/schemas/SimilarityMemoryCard"}
    assert search_items == {"$ref": "#/components/schemas/SimilarityMemoryCard"}
    assert prepare_items == {"$ref": "#/components/schemas/ScoredMemoryCard"}
    assert duplicate_card == {"$ref": "#/components/schemas/SimilarityMemoryCard"}

    list_parameters = {
        parameter["name"]: parameter
        for parameter in committed["paths"]["/v1/memories"]["get"]["parameters"]
    }
    assert list_parameters["limit"]["schema"] == {
        "default": 50,
        "maximum": 200,
        "minimum": 1,
        "title": "Limit",
        "type": "integer",
    }
    assert list_parameters["offset"]["schema"]["default"] == 0
    assert list_parameters["offset"]["schema"]["minimum"] == 0
    list_schema = committed["components"]["schemas"]["MemoryListResponse"]
    assert set(list_schema["required"]) == {"items", "total", "limit", "offset"}
