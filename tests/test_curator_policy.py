"""M3LF / FL-154: a real Palace retains each curator policy across service instances."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from spine.curation.contracts import HealthFinding, PalaceHealthReport
from spine.curation.policy import CuratorPolicy
from spine.curation.provider import CuratorProviderError, OpenRouterCuratorProvider
from spine.ids import mint_ulid
from spine.model_policy import BenchmarkModel, ModelCatalog, ModelRoute


async def test_curator_policy_is_owned_validated_persistent_and_append_only(
    memory_client, memory_session_factory
):
    """A-074: App settings configures the Palace role durably and privately (FL-154)."""
    path = "/v1/curation/model-policy"
    response = await memory_client.get(path, params={"principal_id": "local"})
    assert response.status_code == 200
    assert response.json()["policy"].startswith("pinned:openrouter:")
    for policy in ("pinned:openrouter:openai/gpt-4.1-mini", "max", "elbow", "floor:50"):
        response = await memory_client.put(path, json={"principal_id": "local", "policy": policy})
        assert response.status_code == 200, response.text
        restored = CuratorPolicy(memory_session_factory, "anthropic:claude-sonnet-4-6")
        assert await restored.read("local") == policy
    for principal, policy, status in (("outside", "max", 403), ("local", "floor:-1", 422)):
        response = await memory_client.put(path, json={"principal_id": principal, "policy": policy})
        assert response.status_code == status
    response = await memory_client.get(path, params={"principal_id": "outside"})
    assert response.status_code == 403
    async with memory_session_factory() as session:
        assert await session.scalar(text("SELECT count(*) FROM curator_model_policy")) == 4
        with pytest.raises(DBAPIError):
            await session.execute(text("DELETE FROM curator_model_policy"))


async def test_curator_policy_selects_real_request_model_and_stays_fixed_for_a_pass(
    memory_session_factory,
    monkeypatch,
):
    """FL-154 / A-021 / r9-6: pinned and strongest policies govern future passes."""
    calls = []
    receipts = []
    malformed = False
    first, second = mint_ulid(), mint_ulid()

    def respond(request):
        calls.append(json.loads(request.content)["model"])
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "{}"
                            if malformed
                            else json.dumps({"action": "keep", "rationale": "No change is needed."})
                        }
                    }
                ],
                "usage": {"cost": "0.001"},
            },
        )

    class Spend:
        async def append(self, events):
            receipts.extend(events)

    policy = CuratorPolicy(memory_session_factory, "anthropic:claude-sonnet-4-6")
    await policy.save("local", "pinned:openrouter:openai/gpt-4.1-mini")
    finding = HealthFinding(
        ordinal=0, kind="keyword", memory_ids=[uuid4()], evidence={}, fingerprint="a" * 64
    )
    report = PalaceHealthReport(
        principal_id="local",
        as_of=datetime.now(UTC),
        corpus_revision="one",
        active_units=1,
        clusters=[],
        findings=[finding],
        keyword_coverage_percent="100",
        stats_delta={},
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = OpenRouterCuratorProvider(
            api_key="test",
            model="anthropic:claude-sonnet-4-6",
            spend_service=Spend(),
            client=client,
            policy=policy,
        )
        await provider.verdict(finding, report, run_uid=first, machine_id="local-machine")
        await policy.save("local", "pinned:openrouter:openai/gpt-4.1")
        await provider.verdict(finding, report, run_uid=first, machine_id="local-machine")
        await provider.verdict(finding, report, run_uid=second, machine_id="local-machine")

        async def catalog():
            return ModelCatalog(
                rows=tuple(
                    BenchmarkModel(name, Decimal(score), Decimal(1), Decimal(1))
                    for name, score in (("vendor/small", "20"), ("vendor/strong", "80"))
                ),
                model_routes={
                    name: ModelRoute(name, 100_000) for name in ("vendor/small", "vendor/strong")
                },
                fetched_at=datetime.now(UTC),
            )

        monkeypatch.setattr(provider._catalog, "load", catalog)
        await policy.save("local", "max")
        await provider.verdict(finding, report, run_uid=mint_ulid(), machine_id="local-machine")
        malformed = True
        with pytest.raises(CuratorProviderError, match="malformed"):
            await provider.verdict(finding, report, run_uid=mint_ulid(), machine_id="local-machine")
        await provider.aclose()
    assert calls == [
        "openai/gpt-4.1-mini",
        "openai/gpt-4.1-mini",
        "openai/gpt-4.1",
        "vendor/strong",
        "vendor/strong",
    ]
    assert [receipt.model for receipt in receipts] == calls
