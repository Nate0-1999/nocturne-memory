"""Trace real provider replay matches in the disposable Palace without adopting."""

import asyncio
import json
import os
from pathlib import Path

from harness.onboarding import load_config

root = Path("/Users/nateoswalt/relay/m6cp")
c = load_config(home=root / "scratch/local")
os.environ.update(c.process_environment())
os.environ.update(SPINE_OWNER_PRINCIPAL_ID=c.principal_id, SPINE_CHAT_MODEL="openai:gpt-4.1-mini")


async def main():
    from spine.main import create_app

    app = create_app()
    learner = app.state.compaction_learner
    trace = []
    original = learner.embeddings

    class Embeddings:
        async def embed_with_receipt(self, texts, context):
            vectors = await original.embed_with_receipt(texts, context)
            trace.append(
                {
                    "texts": texts,
                    "cosines": [
                        [
                            sum(a * b for a, b in zip(x, y, strict=True))
                            / max(
                                1e-12, sum(a * a for a in x) ** 0.5 * sum(b * b for b in y) ** 0.5
                            )
                            for y in vectors
                        ]
                        for x in vectors
                    ],
                }
            )
            return vectors

    learner.embeddings = Embeddings()
    async with learner.sessions() as session:
        prepared = await learner.prepare(session, "trace-replay", memory_size=16, preview=True)
        await session.rollback()
    (root / "logs/replay-match-trace.json").write_text(
        json.dumps(
            {"traces": trace, "scores": prepared["scores"], "token_sizes": prepared["token_sizes"]},
            default=str,
            indent=2,
        )
    )
    print(
        "Traced",
        len(trace),
        "real replay embedding requests; matched",
        len(prepared["token_sizes"]),
    )


asyncio.run(main())
