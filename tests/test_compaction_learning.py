"""FL-061: model drift must never replace a better compaction generation."""

from uuid import UUID

import httpx
import pytest
from sqlalchemy import text

from spine.config import Settings
from spine.embeddings import EmbeddingTransportError
from spine.ids import mint_ulid
from spine.learner.compaction import CompactionLearner
from spine.learner.contracts import CompactionTrigger


@pytest.mark.parametrize(
    "answers,attempts,went_back_after",
    [
        ([httpx.ReadTimeout("silent"), 200], 2, "ReadTimeout"),
        ([503, 200], 2, "HTTP 503"),
        ([429, 200], 2, "HTTP 429"),
        ([httpx.ReadTimeout("silent"), httpx.ReadTimeout("silent"), 200], 2, None),
        ([400, 200], 1, None),
    ],
)
async def test_stalled_backtest_request_goes_back_once(
    monkeypatch, answers, attempts, went_back_after
):
    """F169 / PLAN M6CP: one provider stall must not fail a fit of many paid calls.

    A timeout, 429 or 5xx goes back once (v2.133's convention), never twice; any other
    answer is not retried. Only the request that returned a cost is receipted.
    """
    seen, receipts, costs = [], [], []

    def provider(request):
        answer = answers[len(seen)]
        seen.append(request)
        if isinstance(answer, Exception):
            raise answer
        if answer != 200:
            return httpx.Response(answer, request=request)
        return httpx.Response(200, request=request, json={
            "id": "gen-1",
            "usage": {"cost": 0.002},
            "choices": [{"message": {"content": '{"facts": ["Keep the copper notebook."]}'}}],
        })

    class Spend:
        async def append(self, events):
            receipts.extend(events)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(provider), **kw)
    )
    learner = CompactionLearner(
        None,
        principal="owner",
        settings=Settings(
            database_url="postgresql+asyncpg://unused", token="unused", openai_api_key="unused"
        ),
        embeddings=None,
        spend=Spend(),
    )
    if went_back_after:
        reply = await learner._call("Replay this span.", mint_ulid(), costs, kind="replay")
        assert reply == {"facts": ["Keep the copper notebook."]}
        assert [str(receipt.cost_usd) for receipt in receipts] == ["0.002"]
        assert receipts[0].meta["went_back_after"] == went_back_after
        assert costs == [receipts[0].event_uid]
    else:
        with pytest.raises((httpx.ReadTimeout, httpx.HTTPStatusError)):
            await learner._call("Replay this span.", mint_ulid(), costs, kind="replay")
        assert receipts == [] and costs == []
    assert len(seen) == attempts


@pytest.mark.parametrize("preview,size", [(False, 128), (False, 64), (True, 64)])
async def test_losing_or_verification_replay_never_changes_instructions(
    memory_session_factory, preview, size
):
    """SPEC D.2 144 / PLAN M6CP: a model miss cannot cause an unsafe instruction write.

    F169: the first embedding request stalls and goes back once; the fit still completes.
    """

    class Embeddings:
        stalled = False

        async def embed_with_receipt(self, texts, context):
            if not self.stalled:
                self.stalled = True
                raise EmbeddingTransportError("stalled") from httpx.ReadTimeout("stalled")
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

    async def model_call(prompt, uid, costs, **kwargs):
        nonlocal calls
        calls += 1
        if kwargs.get("kind") != "replay":
            return {"instructions": "Forget decisions: {messages}", "memory_size": size}
        return {"facts": ["Keep the copper notebook."] if "Remember decisions:" in prompt else []}

    learner._call = model_call
    async with memory_session_factory() as session, session.begin():
        result = await learner.prepare(session, mint_ulid(), preview=preview)
        assert result["better"] is False
        await learner.record_fit(session, result, verdict="not_better")
    assert result["scores"]["candidate"]["score"] < result["scores"]["incumbent"]["score"]
    snapshot = await learner.snapshot()
    assert snapshot["active"] == policy
    assert snapshot["generations"][-1]["parent"] == "baseline"
    assert snapshot["generations"][-1]["adopted"] is False
