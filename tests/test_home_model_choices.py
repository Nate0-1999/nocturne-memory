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


def test_serving_refusal_names_unknown_switch_only_when_it_would_admit_a_provider():
    """SPEC C.5 / SD-100: incomplete broker location data needs an actionable refusal without
    suggesting an override that cannot admit any available provider."""
    from spine.model_countries import CountryCatalog, CountryFilters

    locations = {"partial": ("FR", []), "wrong": ("US", []), "mixed": ("FR", ["US"])}
    models = {"maker/possible": ["wrong", "partial"], "maker/excluded": ["wrong", "mixed"]}
    filters = CountryFilters.model_validate({"serving": {"mode": "allow", "countries": ["FR"]}})
    catalog = CountryCatalog(filters, locations, models)
    for model, reason in (
        (
            "maker/possible",
            "A provider is missing location details. "
            "Select “Keep unknown provider countries” to allow it.",
        ),
        (
            "maker/excluded",
            "No allowed provider serves this model. Change the filters or pick another model.",
        ),
    ):
        assert catalog.assess(model) == (reason, [])
        assert catalog.describe(model)["country_exclusion"] == reason


@pytest.mark.parametrize("source", ["openai", "anthropic"])
def test_direct_maker_matches_the_broker_and_local_maker_stays_unknown(source):
    """SPEC C.5 / SD-100: changing the transport must not bypass a maker exclusion."""
    from spine.model_countries import CountryCatalog, CountryFilters

    direct = f"{source}:example"
    broker = f"openrouter:{source}/example"
    for countries, keep, reason in [
        (["US"], True, "Maker country US is excluded."),
        (["CN"], False, None),
    ]:
        filters = CountryFilters.model_validate(
            {"maker": {"countries": countries, "keep_unknown": keep}}
        )
        catalog = CountryCatalog(filters, {}, {})
        assert catalog.assess(direct) == catalog.assess(broker) == (reason, [])
        assert catalog.describe(direct)["maker_country"] == "US"
        assert catalog.describe(direct)["maker_source"] == catalog.describe(broker)["maker_source"]
        assert catalog.describe(f"local:{source}/example")["maker_country"] is None


@pytest.mark.asyncio
async def test_named_choice_checks_the_route_returned_by_the_adapter(tmp_path):
    """SPEC C.5 / SD-100: an adapter-normalized name must obey the same maker rule
    when chosen explicitly, pinned, and restored in a conversation."""
    from spine.model_countries import CountryFilters

    class AliasedCatalog:
        async def load_named_route(self, name):
            assert name == "alias/example"
            return ModelRoute("openai/example", 4096), datetime.now(UTC)

    choices = HomeModelChoices(tmp_path)
    choices.countries.save(
        CountryFilters.model_validate({"maker": {"countries": ["US"], "keep_unknown": True}})
    )
    alias = "openrouter:alias/example"
    resolver = ModelPolicyResolver(
        policy=f"pinned:{alias}",
        static_model=None,
        static_context_tokens=4096,
        catalog=AliasedCatalog(),
        choices=choices,
    )
    with pytest.raises(NamedModelResolutionError, match="Maker country US"):
        await resolver.resolve_named("new", alias)
    with pytest.raises(ModelCatalogUnavailable, match="Maker country US"):
        await resolver.resolve_hydrated("saved", alias)
    with pytest.raises(ModelCatalogUnavailable, match="Maker country US"):
        await resolver.resolve("pinned")
