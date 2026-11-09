"""FL-061: model drift must never replace a better compaction generation."""

from uuid import UUID

import pytest
from sqlalchemy import text

from spine.config import Settings
from spine.ids import mint_ulid
from spine.learner.compaction import CompactionLearner
from spine.learner.contracts import CompactionTrigger


@pytest.mark.parametrize("preview", [False, True])
async def test_losing_or_verification_replay_never_changes_instructions(
    memory_session_factory, preview
):
    """SD-085: a model omitting a used fact cannot cause an unsafe instruction write."""

    class Embeddings:
        async def embed_with_receipt(self, texts, context):
            return [
                [1.0, 0.0] if value == "Keep the copper notebook." else [0.0, 1.0]
                for value in texts
            ]

    learner = CompactionLearner(
        memory_session_factory,
        principal="owner",
        settings=Settings(database_url="postgresql+asyncpg://unused", token="unused"),
        embeddings=Embeddings(),
        spend=None,
    )
    policy = {
        "id": "baseline",
        "parent": None,
        "instructions": "Remember decisions: {messages}",
        "memory_size": 128,
    }
    for index in range(2):
        await learner.record(
            CompactionTrigger(
                event_uid=mint_ulid(),
                thread_id=UUID(int=index + 1),
                record={
                    "policy": policy,
                    "source": "Keep the copper notebook.",
                    "machine_id": "studio",
                    "facts": [
                        {"memory_id": str(UUID(int=index + 1)), "body": "Keep the copper notebook."}
                    ],
                },
            ),
            "owner",
        )
        async with memory_session_factory() as session, session.begin():
            await session.execute(
                text("""INSERT INTO creation_outcome VALUES
                (:event, :memory, 'owner', 'studio', 'compaction', 'used', 'cited', now())"""),
                {"event": mint_ulid(), "memory": UUID(int=index + 1)},
            )
    calls = 0

    async def model_call(prompt, uid, costs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"instructions": "Forget decisions: {messages}", "memory_size": 64}
        return {"facts": ["Keep the copper notebook."] if calls == 2 else []}

    learner._call = model_call
    result = await learner.retrain(mint_ulid(), preview=preview)
    assert result["verdict"] == ("preview_only" if preview else "not_better")
    assert result["scores"]["candidate"]["score"] < result["scores"]["incumbent"]["score"]
    snapshot = await learner.snapshot()
    assert snapshot["active"] == policy
    assert snapshot["generations"][-1]["parent"] == "baseline"
    assert snapshot["generations"][-1]["adopted"] is False
