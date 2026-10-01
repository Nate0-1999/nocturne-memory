"""M3LF / FL-154: versioned Palace policy, using the same selector as other roles."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spine.ids import mint_ulid
from spine.model_policy import parse_model_policy


class CuratorPolicy:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], default_model: str):
        self._sessions = sessions
        self.default = f"pinned:{default_model}"

    async def read(self, principal_id: str) -> str:
        async with self._sessions() as session:
            policy = await session.scalar(
                text(
                    "SELECT policy FROM curator_model_policy WHERE principal_id = :principal "
                    "ORDER BY changed_at DESC, event_uid DESC LIMIT 1"
                ),
                {"principal": principal_id},
            )
        return policy or self.default

    async def save(self, principal_id: str, policy: str) -> str:
        parse_model_policy(policy)
        async with self._sessions() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO curator_model_policy (event_uid, principal_id, policy) "
                    "VALUES (:event, :principal, :policy)"
                ),
                {"event": mint_ulid(), "principal": principal_id, "policy": policy},
            )
        return policy
