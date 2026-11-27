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


@pytest.mark.asyncio
async def test_maker_filter_excludes_an_online_and_saved_pick_and_explicit_pin(tmp_path):
    """SPEC C.5 / SD-100: a home must not send a filtered maker's model through a saved pick,
    a pin, hydration or a newly computed strategy after a policy change."""
    from spine.model_countries import CountryFilters

    choices = HomeModelChoices(tmp_path)
    slug = "mistralai/example"
    route = ModelRoute(slug, 1024)
    snapshot = ModelCatalog(
        (BenchmarkModel(slug, Decimal(20), Decimal(1), Decimal(1)),),
        {slug: route},
        datetime.now(UTC),
    )

    class Catalog:
        async def load(self):
            return snapshot

        async def load_named_route(self, model_id):
            return route, snapshot.fetched_at

    resolver = ModelPolicyResolver(
        policy="elbow",
        static_model=None,
        static_context_tokens=None,
        catalog=Catalog(),
        choices=choices,
    )
    pick = await resolver.preview()
    choices.countries.save(CountryFilters.model_validate({"maker": {"countries": ["FR"]}}))
    assert not choices.eligible(snapshot).rows
    with pytest.raises(ModelCatalogUnavailable, match="Maker country FR"):
        await resolver.preview()
    with pytest.raises(NamedModelResolutionError, match="Maker country FR"):
        await resolver.resolve_named("explicit", pick.model)
    with pytest.raises(ModelCatalogUnavailable, match="Maker country FR"):
        await resolver.resolve_hydrated("existing", pick.model)
    with pytest.raises(ModelCatalogUnavailable, match="Maker country FR"):
        HomeModelChoices(tmp_path).offline("elbow")


def test_country_rules_only_judge_their_own_unknowns():
    """SPEC C.5 / SD-100: missing fleet data cannot disqualify a known maker, and retaining
    an unknown must neither waive a known country nor relax the other rule."""
    from spine.model_countries import CountryCatalog, CountryFilters

    providers = {"partial": ("US", []), "wrong": ("FR", []), "known": ("US", ["US"])}
    routes = {"mistralai/test": ["partial"], "unsourced/test": list(providers)}
    maker = {"countries": ["CN"]}
    serving = {"mode": "allow", "countries": ["US"]}

    def assess(model, **rules):
        return CountryCatalog(CountryFilters.model_validate(rules), providers, routes).assess(model)

    assert assess("mistralai/test", maker=maker) == (None, [])
    assert assess("~mistralai/test", maker=maker) == (None, [])
    assert assess("~mistralai/test", maker={"countries": ["FR"]})[0]
    legacy = CountryFilters.model_validate(
        {"keep_unknown": True, "serving": {"keep_unknown": False}}
    )
    assert legacy.maker.keep_unknown and not legacy.serving.keep_unknown
    assert assess("unsourced/test", serving=serving) == (None, ["known"])
    assert assess("unsourced/test", maker=maker, serving={**serving, "keep_unknown": True})[0]
    assert assess("unsourced/test", maker={**maker, "keep_unknown": True}, serving=serving) == (
        None,
        ["known"],
    )
    assert assess("unsourced/test", serving={**serving, "keep_unknown": True}) == (
        None,
        ["partial", "known"],
    )
