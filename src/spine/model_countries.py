"""Country choices for the home's model policy. Locations describe makers and provider fleets."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Catalog makers, including the catalog's ~maker aliases. Routing services with
# an undisclosed or variable underlying maker have no entry.
_META = (
    "US",
    "https://www.sec.gov/Archives/edgar/data/1326801/000162828026003942/meta-20251231.htm",
)
MAKERS = {
    "aion-labs": ("PL", "https://www.aionlabs.ai/privacy/"),
    "amazon": ("US", "https://ir.aboutamazon.com/faqs/default.aspx"),
    "anthropic": ("US", "https://www.anthropic.com/careers"),
    "baidu": ("CN", "https://ir.baidu.com/shareholder-services/investor-faqs/"),
    "bytedance": ("CN", "https://www.bytedance.com/about/"),
    "bytedance-seed": ("CN", "https://seed.bytedance.com/en/privacy-policy"),
    "cohere": ("CA", "https://cohere.com/about"),
    "deepseek": ("CN", "https://cdn.deepseek.com/policies/en-US/deepseek-terms-of-use.html"),
    "elorian": ("US", "https://elorian.ai/privacy"),
    "fireworks": ("US", "https://fireworks.ai/news/fireworks-ai-raises-250m-series-c"),
    "google": ("US", "https://policies.google.com/terms?gl=us"),
    "ibm-granite": ("US", "https://www.ibm.com/contact/global"),
    "inclusionai": ("CN", "https://www.antgroup.com/about/our-offices"),
    "inference-net": ("US", "https://inference.net/terms-of-service/"),
    "kt": ("KR", "https://corp.kt.com/eng/html/intro/info/locations_01.html"),
    "liquid": ("US", "https://www.liquid.ai/privacy-policy"),
    "meta": _META,
    "meta-llama": _META,
    "microsoft": ("US", "https://www.microsoft.com/en-us/about/office-locations"),
    "minimax": (
        "CN",
        "https://www.hkexnews.hk/listedco/listconews/sehk/2026/0519/2026051901411.pdf",
    ),
    "mistralai": ("FR", "https://mistral.ai/legal/"),
    "moondream": ("US", "https://moondream.ai/about/us"),
    "moonshotai": ("CN", "https://www.moonshot.cn/about"),
    "morph": ("US", "https://www.ycombinator.com/companies/morph"),
    "motif-technologies": ("KR", "https://motiftech.io/en/contact/"),
    "nex-agi": ("CN", "https://nex.sii.edu.cn/n1/en/"),
    "nousresearch": ("US", "https://portal.nousresearch.com/terms"),
    "nvidia": ("US", "https://www.nvidia.com/en-us/contact/"),
    "openai": ("US", "https://openai.com/policies/us-privacy-policy/revisions/2026-02-09/"),
    "particle-ai": ("US", "https://particle.ai/about/"),
    "perceptron": ("US", "https://akshatsh.github.io/experience/"),
    "perplexity": (
        "US",
        "https://www.sec.gov/Archives/edgar/data/1972171/000156761923005521/xslFormDX01/primary_doc.xml",
    ),
    "prism-ml": ("US", "https://prismml.com/news/prismml-launches-bonsai-2-27b"),
    "qwen": (
        "CN",
        "https://data.alibabagroup.com/ir_filings/SEC/BABA/000119312526231755/baba-20260331.htm",
    ),
    "radixark": ("US", "https://jobs.accel.com/companies/radixark-2"),
    "recursive": ("US", "https://recursive.com/legal/terms-of-use"),
    "reflection": (
        "US",
        "https://www.prnewswire.com/news-releases/reflection-and-shinsegae-group-to-build-korean-sovereign-ai-factory-302715111.html",
    ),
    "rekaai": ("US", "https://reka.ai/contact"),
    "relace": ("US", "https://www.ycombinator.com/companies/relace"),
    "ricursiveai": (
        "US",
        "https://www.prnewswire.com/news-releases/ricursive-intelligence-launches-frontier-ai-lab-to-transform-semiconductor-design-and-accelerate-path-toward-artificial-superintelligence-302630776.html",
    ),
    "sakana": ("JP", "https://sakana.ai/company-info/"),
    "scaledown": ("US", "https://www.linkedin.com/company/scaledown-ai"),
    "stepfun": ("CN", "https://chat.stepfun.com/legal/privacy"),
    "tencent": (
        "CN",
        "https://static.www.tencent.com/uploads/2026/04/09/62d786fcf3d3c8cb7e54791ee95439ac.pdf",
    ),
    "thinkingmachines": ("US", "https://thinkingmachines.ai/legal/privacy/"),
    "tngtech": ("DE", "https://www.tngtech.com/en/about-us/locations/"),
    "typesafe": ("US", "https://typesafe.ai/legal/terms"),
    "upstage": ("KR", "https://www.upstage.ai/news/solar-pro-aws"),
    "writer": ("US", "https://writer.com/legal/privacy/"),
    "x-ai": ("US", "https://www.sec.gov/edgar/browse/?CIK=0002002695"),
    "xiaomi": (
        "CN",
        "https://ir.mi.com/system/files-encrypted/nasdaq_kms/assets/2026/04/28/5-29-08/Xiaomi%202025%20AR_EN.pdf",
    ),
    "z-ai": ("CN", "https://www.hkexnews.hk/listedco/listconews/sehk/2026/0331/2026033101549.pdf"),
}

Country = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]


def _country(value: object) -> str | None:
    if isinstance(value, str) and len(value) == 2 and value.isascii() and value.isalpha():
        return value.upper()
    return None


LOCATION_REFRESH_FAILED = "Provider locations could not be refreshed. Try again."


DIRECT_MAKERS = frozenset({"openai", "anthropic"})


def _maker(model: str, direct_makers: frozenset[str]) -> tuple[str, str] | None:
    if model.startswith(("openai:", "openai-chat:", "openai-responses:")):
        return MAKERS["openai"] if "openai" in direct_makers else None
    if model.startswith("anthropic:"):
        return MAKERS["anthropic"] if "anthropic" in direct_makers else None
    slug = model.removeprefix("openrouter:")
    return MAKERS.get(slug.partition("/")[0].removeprefix("~"))


class CountryRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["allow", "exclude"] = "exclude"
    countries: tuple[Country, ...] = ()
    keep_unknown: bool = False

    def accepts(self, known: Iterable[str]) -> bool:
        return not self.countries or all(
            (code in self.countries) == (self.mode == "allow") for code in known
        )


class CountryFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    maker: CountryRule = Field(default_factory=CountryRule)
    serving: CountryRule = Field(default_factory=CountryRule)

    @model_validator(mode="before")
    @classmethod
    def migrate_shared_unknown_setting(cls, value):
        if not isinstance(value, dict) or "keep_unknown" not in value:
            return value
        converted = dict(value)
        shared = converted.pop("keep_unknown")
        for side in ("maker", "serving"):
            rule = converted.get(side, {})
            if isinstance(rule, dict):
                converted[side] = {"keep_unknown": shared, **rule}
        return converted

    @property
    def active(self) -> bool:
        return bool(self.maker.countries or self.serving.countries)


@dataclass(frozen=True)
class CountryCatalog:
    filters: CountryFilters
    # provider slug -> [reported headquarters or null, list of datacenter countries]
    providers: dict
    # exact request model -> provider slugs with a published available endpoint
    models: dict
    direct_makers: frozenset[str] = DIRECT_MAKERS

    def options(self) -> list[str]:
        return sorted(
            {country for country, _ in MAKERS.values()}
            | {code for hq, centers in self.providers.values() for code in [hq, *centers] if code}
            | set(self.filters.maker.countries)
            | set(self.filters.serving.countries)
        )

    def assess(self, model: str) -> tuple[str | None, list[str]]:
        if not self.filters.active or model.startswith("subscription:"):
            return None, []
        slug = model.removeprefix("openrouter:")
        maker = _maker(model, self.direct_makers)
        if maker and not self.filters.maker.accepts([maker[0]]):
            return f"Maker country {maker[0]} is excluded.", []
        if self.filters.maker.countries and not maker and not self.filters.maker.keep_unknown:
            return "Maker country is unknown.", []
        if not self.filters.serving.countries:
            return None, []
        if model.startswith(
            ("local:", "openai:", "openai-chat:", "openai-responses:", "anthropic:")
        ):
            if self.filters.serving.keep_unknown:
                return None, []
            return "Provider country is unknown.", []
        if slug not in self.models:
            return LOCATION_REFRESH_FAILED, []
        allowed = []
        missing_location = False
        for provider in self.models.get(slug, []):
            hq, centers = self.providers.get(provider, (None, []))
            if not self.filters.serving.accepts(code for code in [hq, *centers] if code):
                continue
            if (not hq or not centers or None in centers) and not self.filters.serving.keep_unknown:
                missing_location = True
                continue
            allowed.append(provider)
        if not allowed:
            if missing_location:
                return (
                    "A provider is missing location details. "
                    "Select “Keep unknown provider countries” to allow it.",
                    [],
                )
            return (
                "No allowed provider serves this model. Change the filters or pick another model.",
                [],
            )
        return None, allowed

    def describe(self, model: str) -> dict:
        slug = model.removeprefix("openrouter:")
        maker = _maker(model, self.direct_makers)
        locations = [self.providers.get(p, (None, [])) for p in self.models.get(slug, [])]
        return {
            "maker_country": maker[0] if maker else None,
            "maker_source": maker[1] if maker else None,
            "headquarters": sorted({hq for hq, _ in locations if hq}),
            "datacenters": sorted({c for _, centers in locations for c in centers if c}),
            "unknown_headquarters": not locations or any(not hq for hq, _ in locations),
            "unknown_datacenters": not locations
            or any(not centers or None in centers for _, centers in locations),
            "country_exclusion": self.assess(model)[0],
        }


class HomeCountries:
    def __init__(self, path: Path, *, direct_makers: frozenset[str] = DIRECT_MAKERS) -> None:
        self.path = path
        self.direct_makers = direct_makers
        self._lock = asyncio.Lock()
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS country_settings "
                "(id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS country_catalog "
                "(id INTEGER PRIMARY KEY, checked REAL, providers TEXT, models TEXT)"
            )

    def filters(self) -> CountryFilters:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT value FROM country_settings WHERE id=1").fetchone()
        return CountryFilters.model_validate_json(row[0]) if row else CountryFilters()

    def save(self, filters: CountryFilters) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR REPLACE INTO country_settings VALUES (1,?)", (filters.model_dump_json(),)
            )

    def _read(self) -> tuple[float, dict, dict]:
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT checked, providers, models FROM country_catalog WHERE id=1"
            ).fetchone()
            if not row or time.time() - row[0] >= 86400:
                return 0, {}, {}
            return row[0], json.loads(row[1]), json.loads(row[2])

    def catalog(self) -> CountryCatalog:
        _, providers, models = self._read()
        return CountryCatalog(self.filters(), providers, models, self.direct_makers)

    async def refresh(
        self, model_ids: Iterable[str], client: httpx.AsyncClient, base_url: str
    ) -> None:
        ids = {model.removeprefix("openrouter:") for model in model_ids}
        async with self._lock:
            checked, providers, models = self._read()
            missing = ids - models.keys()
            if not missing:
                return
            if not checked:
                response = await client.get(f"{base_url}/providers")
                response.raise_for_status()
                providers = {
                    p["slug"]: [
                        _country(p.get("headquarters")),
                        [_country(c) for c in p["datacenters"]]
                        if isinstance(p.get("datacenters"), list)
                        else [],
                    ]
                    for p in response.json()["data"]
                    if p.get("slug")
                }
            limit = asyncio.Semaphore(8)

            async def endpoints(model: str):
                async with limit:
                    response = await client.get(
                        f"{base_url}/models/{quote(model, safe='/:')}/endpoints"
                    )
                    if response.status_code == 404:
                        return model, []
                    response.raise_for_status()
                    rows = response.json()["data"]["endpoints"]
                    return model, sorted(
                        {
                            slug
                            for row in rows
                            if row.get("status") == 0
                            and (slug := (row.get("tag") or "").partition("/")[0])
                        }
                    )

            additions = dict(await asyncio.gather(*(endpoints(m) for m in missing)))
            with sqlite3.connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute(
                    "SELECT checked, providers, models FROM country_catalog WHERE id=1"
                ).fetchone()
                if current and time.time() - current[0] < 86400:
                    checked, providers, models = (
                        current[0],
                        json.loads(current[1]),
                        json.loads(current[2]),
                    )
                models.update(additions)
                db.execute(
                    "INSERT OR REPLACE INTO country_catalog VALUES (1,?,?,?)",
                    (checked or time.time(), json.dumps(providers), json.dumps(models)),
                )
