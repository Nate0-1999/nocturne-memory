"""Async SQLAlchemy engine construction."""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def make_engine(database_url: str, *, pool_size: int = 2, max_overflow: int = 3) -> AsyncEngine:
    """Construct an async engine without opening a connection."""

    return create_async_engine(
        database_url, pool_pre_ping=True, pool_size=pool_size, max_overflow=max_overflow
    )
