"""C.5 defaults and fixed storage-shape configuration tests."""

import asyncio
from contextlib import AsyncExitStack
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest
from conftest import ScriptedEmbeddingProvider
from pydantic import ValidationError

import spine.main as spine_main
from spine.config import Settings
from spine.db.engine import make_engine
from spine.learner.service import LearnerService


def _settings(**overrides: object) -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://unused:unused@localhost/unused",
        token="test-token",
        **overrides,
    )


def test_c5_dedup_and_embedding_defaults_are_exact() -> None:
    """SPEC C.5 is defended by verifying that c5 dedup and embedding defaults are exact; this
    prevents drift in the runtime configuration boundary.
    """
    settings = _settings()

    assert settings.dedup_dup == 0.92
    assert settings.dedup_sim == 0.80
    assert settings.curator_review_sim == 0.70
    assert settings.database_pool_size == 2
    assert settings.database_max_overflow == 3
    assert settings.embed_base_url == "https://openrouter.ai/api/v1"
    assert settings.embed_model == "openai/text-embedding-3-small"
    assert settings.embed_dim == 1536
    assert settings.learner_min_dispositions == 25
    assert settings.learner_holdout_fraction == 0.20
    assert settings.learner_passive_discount == 0.25
    assert settings.learner_pair_margin == 0.05
    assert settings.learner_bias_l2 == 1.0
    assert settings.learner_win_margin == 1.0
    assert settings.retrain_signal_stride == 25
    assert settings.optimization_corpus_max_dispositions == 1000
    assert settings.reconciliation_hours == 24
    assert settings.reconciliation_tolerance_usd == Decimal("0.000001")


@pytest.mark.asyncio
async def test_database_pool_waits_at_the_configured_connection_budget(
    migrated_database_url: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F159 bounds each API's connections instead of exhausting the shared instance."""
    monkeypatch.setenv("SPINE_DATABASE_POOL_SIZE", "1")
    monkeypatch.setenv("SPINE_DATABASE_MAX_OVERFLOW", "1")
    settings = _settings()
    engine = make_engine(migrated_database_url, pool_size=settings.database_pool_size,
                         max_overflow=settings.database_max_overflow)
    try:
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(engine.connect())
            await stack.enter_async_context(engine.connect())
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(engine.connect(), timeout=0.1)
        async with engine.connect() as connection:
            assert (await connection.exec_driver_sql("SELECT 1")).scalar() == 1
    finally:
        await engine.dispose()


def test_curator_band_is_configured_and_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """SPEC v2.128 / FL-058 separates curator configuration from write-time bands."""
    monkeypatch.setenv("SPINE_CURATOR_REVIEW_SIM", "0.75")
    settings = _settings()
    assert settings.curator_review_sim == 0.75
    assert (settings.dedup_dup, settings.dedup_sim) == (0.92, 0.80)
    assert next(row for row in LearnerService.manifest()
                if row["parameter"] == "curator_review_sim") == {
        "parameter": "curator_review_sim", "loop": "creation",
        "floor": None, "status": "configured",
    }


def test_runtime_environment_cannot_override_artifact_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SPEC C.5 is defended by verifying that runtime environment cannot override artifact
    version; this prevents drift in the runtime configuration boundary.
    """
    monkeypatch.setenv("SPINE_VERSION", "not-the-installed-version")

    assert "version" not in Settings.model_fields
    assert not hasattr(_settings(), "version")


def test_config_rejects_overlapping_bands_and_wrong_storage_dimension() -> None:
    """SPEC C.5 is defended by verifying that config rejects overlapping bands and wrong
    storage dimension; this prevents drift in the runtime configuration boundary.
    """
    with pytest.raises(ValidationError, match="dedup_sim must be less than dedup_dup"):
        _settings(dedup_sim=0.92, dedup_dup=0.92)

    with pytest.raises(ValidationError):
        _settings(embed_dim=512)
    with pytest.raises(ValidationError):
        _settings(learner_holdout_fraction=0.5)
    with pytest.raises(ValidationError):
        _settings(learner_passive_discount=0.0)
    with pytest.raises(ValidationError):
        _settings(retrain_signal_stride=0)
    with pytest.raises(
        ValidationError,
        match="optimization_corpus_max_dispositions must be at least learner_min_dispositions",
    ):
        _settings(learner_min_dispositions=26, optimization_corpus_max_dispositions=25)


@pytest.mark.parametrize(
    ("environment", "expected_base_url", "expected_model"),
    [
        ({}, "https://openrouter.ai/api/v1", "openai/text-embedding-3-small"),
        (
            {
                "SPINE_EMBED_BASE_URL": "https://api.openai.com/v1",
                "SPINE_EMBED_MODEL": "text-embedding-3-small",
            },
            "https://api.openai.com/v1",
            "text-embedding-3-small",
        ),
    ],
)
def test_embedding_runtime_wires_default_and_direct_provider_without_network(
    monkeypatch: pytest.MonkeyPatch,
    environment: dict[str, str],
    expected_base_url: str,
    expected_model: str,
) -> None:
    """SPEC C.5 is defended by verifying that embedding runtime wires default and direct
    provider without network; this prevents drift in the runtime configuration boundary.
    """
    monkeypatch.delenv("SPINE_EMBED_BASE_URL", raising=False)
    monkeypatch.delenv("SPINE_EMBED_MODEL", raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    provider = ScriptedEmbeddingProvider()
    provider.aclose = AsyncMock()  # type: ignore[attr-defined]
    fake_router = Mock(return_value=provider)

    def unused_session_factory() -> None:
        raise AssertionError("configuration test must not access Postgres")

    monkeypatch.setattr(spine_main, "build_embedding_router", fake_router)
    app = spine_main.create_app(
        _settings(openai_api_key="compatible-key"),
        session_factory=unused_session_factory,  # type: ignore[arg-type]
    )

    fake_router.assert_called_once()
    routed_settings, receipt_sink = fake_router.call_args.args
    assert routed_settings.openai_api_key.get_secret_value() == "compatible-key"
    assert routed_settings.embed_model == expected_model
    assert routed_settings.embed_dim == 1536
    assert routed_settings.embed_base_url == expected_base_url
    assert receipt_sink is app.state.spend_service
    assert (app.state.reconciliation_service is not None) is (
        expected_base_url == "https://openrouter.ai/api/v1"
    )
