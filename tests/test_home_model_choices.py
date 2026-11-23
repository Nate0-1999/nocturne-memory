from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from spine.model_policy import (
    BenchmarkModel,
    HomeModelChoices,
    ModelCatalog,
    ModelCatalogUnavailable,
    ModelPolicyResolver,
    ModelRoute,
    NamedModelResolutionError,
)


@pytest.mark.asyncio
async def test_strategy_reprices_after_bans_and_keeps_its_own_dated_pick(tmp_path):
    """SPEC C.5 / SD-100: bans change new conversations, survive restart and constrain fallback."""
    choices = HomeModelChoices(tmp_path)
    names = ["minimax/sample", "first/sample", "second/sample"]
    now = datetime.now(UTC)
    snapshot = ModelCatalog(
        tuple(
            BenchmarkModel(name, Decimal(10 - i), Decimal(1), Decimal(2))
            for i, name in enumerate(names)
        ),
        {name: ModelRoute(name, 8192) for name in names},
        now,
    )

    class Catalog:
        async def load(self):
            return snapshot

    def resolver(home, catalog):
        return ModelPolicyResolver(
            policy="elbow",
            static_model=None,
            static_context_tokens=None,
            choices=home,
            catalog=catalog,
        )

    online = resolver(choices, Catalog())
    first = await online.resolve("old")
    assert first.model == "openrouter:first/sample"
    choices.set_ban("model:first/sample", True)
    assert (await online.resolve("old")).model == first.model
    second = await online.resolve("new")
    assert second.model == "openrouter:second/sample"
    with pytest.raises(NamedModelResolutionError, match="banned"):
        await online.resolve_named("explicit", first.model)
    snapshot = replace(snapshot, fetched_at=now + timedelta(days=1))
    await online.preview()
    reopened = HomeModelChoices(tmp_path)
    assert reopened.last("elbow")["changed"] != reopened.last("elbow")["checked"]
    offline = resolver(reopened, None)
    saved = await offline.resolve("offline")
    assert saved.model == second.model and saved.context_tokens == second.context_tokens
    assert str(snapshot.fetched_at.date()) in saved.reason
    reopened.set_ban("maker:second", True)
    with pytest.raises(ModelCatalogUnavailable, match="Reconnect"):
        await offline.resolve("blocked")
    reopened.set_ban("model:first/sample", False)
    assert (await online.resolve("restored")).model == first.model
    reopened.set_ban("maker:minimax", False)
    assert "maker:minimax" not in HomeModelChoices(tmp_path).bans()
    with pytest.raises(ModelCatalogUnavailable, match="Reconnect"):
        await resolver(HomeModelChoices(tmp_path / "fresh"), None).resolve("cold")
