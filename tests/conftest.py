"""Shared API and live-Postgres test fixtures."""

from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Self, cast

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from spine.config import Settings
from spine.db.migrate import upgrade_head
from spine.db.session import make_session_factory
from spine.main import create_app

TOKEN = "p0-test-token"
EMBED_DIM = 1536
ACTIVE_SCORER_VERSION = "m3ms-share-v1"


def basis_vector(index: int) -> list[float]:
    """Return a unit vector useful for exact orthogonal test embeddings."""

    vector = [0.0] * EMBED_DIM
    vector[index] = 1.0
    return vector


def vector_with_cosine(score: float, *, axis: int = 0, other_axis: int = 1) -> list[float]:
    """Return a unit vector whose cosine with ``basis_vector(axis)`` is ``score``."""

    vector = [0.0] * EMBED_DIM
    vector[axis] = score
    vector[other_axis] = (1.0 - score**2) ** 0.5
    return vector


class ScriptedEmbeddingProvider:
    """Small deterministic fake with explicit body-to-vector scripts."""

    model = "test-embedding-1536"
    dimensions = EMBED_DIM

    def __init__(self) -> None:
        self._vectors: dict[str, list[float]] = {}
        self.calls: list[tuple[str, ...]] = []

    def set(self, text_value: str, vector: Sequence[float]) -> Self:
        if len(vector) != self.dimensions:
            raise ValueError(f"test embedding must have {self.dimensions} dimensions")
        self._vectors[text_value] = [float(value) for value in vector]
        return self

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        batch = tuple(texts)
        self.calls.append(batch)
        try:
            return [list(self._vectors[text_value]) for text_value in batch]
        except KeyError as exc:  # pragma: no cover - assertion aid for a broken test
            raise AssertionError(f"no scripted embedding for {exc.args[0]!r}") from exc


def _asyncpg_url(url: str) -> str:
    scheme, separator, rest = url.partition("://")
    if not separator:
        raise ValueError("testcontainers returned a malformed Postgres URL")
    if not scheme.startswith("postgres"):
        raise ValueError("testcontainers returned a non-Postgres URL")
    return f"postgresql+asyncpg://{rest}"


@pytest.fixture(scope="session")
def migrated_database_url() -> Iterator[str]:
    """Start pgvector Postgres and apply the production Alembic migration."""

    with PostgresContainer(
        "pgvector/pgvector:pg16",
        username="spine",
        password="spine",
        dbname="spine",
    ) as postgres:
        database_url = _asyncpg_url(postgres.get_connection_url())
        upgrade_head(database_url)
        yield database_url


@pytest.fixture
def app() -> FastAPI:
    def unused_session_factory() -> None:
        raise AssertionError("this contract-only app must not access Postgres")

    settings = Settings(
        database_url="postgresql+asyncpg://unused:unused@localhost/unused",
        token=TOKEN,
    )
    return create_app(
        settings,
        session_factory=cast(async_sessionmaker[AsyncSession], unused_session_factory),
        embedding_provider=ScriptedEmbeddingProvider(),
    )


@pytest.fixture
def embedding_provider() -> ScriptedEmbeddingProvider:
    return ScriptedEmbeddingProvider()


@pytest.fixture
async def memory_session_factory(
    migrated_database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Give each API test an empty database while reusing the migrated container."""

    engine = create_async_engine(migrated_database_url)
    session_factory = make_session_factory(engine)
    truncate = text(
        "TRUNCATE curator_progress, creation_outcome, curator_action, curator_verdict, "
        "curator_finding, curator_run, "
        "curator_trigger_state, transcript_record, optimization_run_adoption, "
        "optimization_run, learner_run, scorer_activation, "
        "approval_decision, "
        "approval_queue_item, "
        "memory_edge, spend_reconciliation, spend_event, injection_event_annotation, "
        "injection_event, thread, memory_revision, memory_unit "
        "RESTART IDENTITY CASCADE"
    )
    async with engine.begin() as connection:
        await connection.execute(truncate)
        await connection.execute(
            text(
                "DELETE FROM scorer_config WHERE version NOT IN "
                "('v0', 'm3f-location-v1', 'm3ti-thread-v1', :active_version)"
            ),
            {"active_version": ACTIVE_SCORER_VERSION},
        )
        await connection.execute(text("UPDATE scorer_config SET active = false WHERE active"))
        await connection.execute(
            text("UPDATE scorer_config SET active = true WHERE version = :active_version"),
            {"active_version": ACTIVE_SCORER_VERSION},
        )
    try:
        yield session_factory
    finally:
        async with engine.begin() as connection:
            await connection.execute(truncate)
            await connection.execute(
                text(
                    "DELETE FROM scorer_config WHERE version NOT IN "
                    "('v0', 'm3f-location-v1', 'm3ti-thread-v1', :active_version)"
                ),
                {"active_version": ACTIVE_SCORER_VERSION},
            )
            await connection.execute(text("UPDATE scorer_config SET active = false WHERE active"))
            await connection.execute(
                text("UPDATE scorer_config SET active = true WHERE version = :active_version"),
                {"active_version": ACTIVE_SCORER_VERSION},
            )
        await engine.dispose()


@pytest.fixture
async def memory_app(
    memory_session_factory: async_sessionmaker[AsyncSession],
    embedding_provider: ScriptedEmbeddingProvider,
) -> FastAPI:
    settings = Settings(
        database_url="postgresql+asyncpg://unused:unused@localhost/unused",
        token=TOKEN,
    )
    return create_app(
        settings,
        session_factory=memory_session_factory,
        embedding_provider=embedding_provider,
    )


@pytest.fixture
async def memory_client(memory_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=memory_app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        yield client
