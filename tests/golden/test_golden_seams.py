"""M3B zero-regression goldens for Spine-owned provider and config seams."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import httpx
import pytest

import spine.embeddings as embedding_module
from spine.config import Settings
from spine.embeddings import EmbeddingReceiptContext, OpenAIEmbeddingProvider
from spine.spend.contracts import SpendEventInput

SNAPSHOT_DIR = Path(__file__).with_name("snapshots")
FIXED_EVENT_UID = "01KZP4YBXKCZ746CE679F1C6E5"
FIXED_RUN_ID = "01KZW0YASXW9F4ZJ4V7KTFXHV2"
FIXED_PROMPT_ID = "01KZQ9DN2JHFRQ2WEC5HTBPRZQ"


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz: object = None) -> _FrozenDateTime:
        return cls(2026, 8, 15, 12, 34, 56, tzinfo=tz or UTC)  # type: ignore[arg-type]


class _ReceiptSink:
    def __init__(self) -> None:
        self.events: list[SpendEventInput] = []

    async def append(self, events: Sequence[SpendEventInput]) -> int:
        self.events.extend(events)
        return len(events)


def _settings(**overrides: object) -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://unused:unused@localhost/unused",
        token="test-token",
        **overrides,
    )


def test_runtime_config_resolution_keeps_the_current_provider_and_scorer_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SPEC C.5 / A-031 pin config resolution; SD-094 / A-086 authorize the new default."""

    for field in Settings.model_fields:
        monkeypatch.delenv(f"SPINE_{field.upper()}", raising=False)

    defaults = _settings()
    monkeypatch.setenv("SPINE_EMBED_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("SPINE_EMBED_MODEL", "text-embedding-3-small")
    direct = _settings()

    def projection(settings: Settings) -> dict[str, object]:
        return {
            "tau": settings.tau,
            "top_k": settings.top_k,
            "near_miss_k": settings.near_miss_k,
            "memory_context_share": settings.memory_context_share,
            "half_life_time_days": settings.half_life_time_days,
            "half_life_hist_days": settings.half_life_hist_days,
            "dedup_dup": settings.dedup_dup,
            "dedup_sim": settings.dedup_sim,
            "never_bias_step": settings.never_bias_step,
            "quarantine_kills": settings.quarantine_kills,
            "candidate_pool": settings.candidate_pool,
            "embed_base_url": settings.embed_base_url,
            "embed_model": settings.embed_model,
            "embed_dim": settings.embed_dim,
            "memory_max_tokens": settings.memory_max_tokens,
            "label_max": settings.label_max,
            "chat_model": settings.chat_model,
            "spend_view_refresh_seconds": settings.spend_view_refresh_seconds,
            "reconciliation_hours": settings.reconciliation_hours,
            "reconciliation_tolerance_usd": str(settings.reconciliation_tolerance_usd),
            "learner_min_dispositions": settings.learner_min_dispositions,
            "learner_holdout_fraction": settings.learner_holdout_fraction,
            "learner_passive_discount": settings.learner_passive_discount,
            "learner_pair_margin": settings.learner_pair_margin,
            "learner_bias_l2": settings.learner_bias_l2,
            "learner_win_margin": settings.learner_win_margin,
            "retrain_signal_stride": settings.retrain_signal_stride,
            "optimization_corpus_max_dispositions": (settings.optimization_corpus_max_dispositions),
            "graph_edge_sim": settings.graph_edge_sim,
        }

    expected_defaults = {
        "tau": 0.55,
        "top_k": 8,
        "near_miss_k": 3,
        "memory_context_share": 0.10,
        "half_life_time_days": 14,
        "half_life_hist_days": 7,
        "dedup_dup": 0.92,
        "dedup_sim": 0.80,
        "never_bias_step": -0.15,
        "quarantine_kills": 3,
        "candidate_pool": 50,
        "embed_base_url": "https://openrouter.ai/api/v1",
        "embed_model": "openai/text-embedding-3-small",
        "embed_dim": 1536,
        "memory_max_tokens": 128,
        "label_max": 64,
        "chat_model": "openrouter:z-ai/glm-5.3-flash",
        "spend_view_refresh_seconds": 60,
        "reconciliation_hours": 24,
        "reconciliation_tolerance_usd": str(Decimal("0.000001")),
        "learner_min_dispositions": 25,
        "learner_holdout_fraction": 0.20,
        "learner_passive_discount": 0.25,
        "learner_pair_margin": 0.05,
        "learner_bias_l2": 1.0,
        "learner_win_margin": 1.0,
        "retrain_signal_stride": 25,
        "optimization_corpus_max_dispositions": 1000,
        "graph_edge_sim": 0.75,
    }
    expected_direct = {
        **expected_defaults,
        "embed_base_url": "https://api.openai.com/v1",
        "embed_model": "text-embedding-3-small",
    }

    assert {"defaults": projection(defaults), "direct": projection(direct)} == {
        "defaults": expected_defaults,
        "direct": expected_direct,
    }


@pytest.mark.asyncio
async def test_embedding_request_response_and_receipt_match_the_checked_in_golden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-024 and P4 pin the exact brokered embedding request, result, and receipt."""

    monkeypatch.setattr(embedding_module, "mint_ulid", lambda: FIXED_EVENT_UID)
    monkeypatch.setattr(embedding_module, "datetime", _FrozenDateTime)
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "authorization": request.headers["authorization"],
                "body": json.loads(request.content),
            }
        )
        return httpx.Response(
            200,
            headers={"x-request-id": "embed-request-golden"},
            json={
                "usage": {
                    "prompt_tokens": 7,
                    "cost": "0.000070",
                    "cache_discount": 0,
                },
                "data": [
                    {"index": 1, "embedding": [4, 5, 6]},
                    {"index": 0, "embedding": [1, 2, 3]},
                ],
            },
        )

    sink = _ReceiptSink()
    context = EmbeddingReceiptContext(
        principal_id="owner",
        machine_id="workstation",
        origin_agent="harness-chat",
        thread_id=UUID("11111111-1111-4111-8111-111111111111"),
        run_id=FIXED_RUN_ID,
        prompt_id=FIXED_PROMPT_ID,
        memory_id=UUID("22222222-2222-4222-8222-222222222222"),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAIEmbeddingProvider(
            api_key=" golden-key ",
            model="openai/text-embedding-3-small",
            dimensions=3,
            base_url="https://openrouter.ai/api/v1/",
            client=client,
            receipt_sink=sink,
        )
        vectors = await provider.embed_with_receipt(["first", "second"], context)

    assert len(requests) == 1
    assert len(sink.events) == 1
    observed = {
        "request": requests[0],
        "vectors": vectors,
        "receipt": sink.events[0].model_dump(mode="json", exclude_none=True),
    }
    expected = json.loads((SNAPSHOT_DIR / "embedding_broker.json").read_text())

    assert observed == expected
