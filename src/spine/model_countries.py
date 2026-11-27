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
from pydantic import BaseModel, ConfigDict, Field

# Exact catalog author slugs. Each source identifies the maker's home country.
_META = (
    "US",
    "https://www.sec.gov/Archives/edgar/data/1326801/000162828026003942/meta-20251231.htm",
)
MAKERS = {
    "anthropic": ("US", "https://www.anthropic.com/careers"),
    "deepseek": ("CN", "https://cdn.deepseek.com/policies/en-US/deepseek-terms-of-use.html"),
    "meta": _META,
    "meta-llama": _META,
    "minimax": (
        "CN",
        "https://www.hkexnews.hk/listedco/listconews/sehk/2026/0519/2026051901411.pdf",
    ),
    "mistralai": ("FR", "https://mistral.ai/legal/"),
    "openai": ("US", "https://openai.com/policies/us-privacy-policy/revisions/2026-02-09/"),
}

Country = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]


def _country(value: object) -> str | None:
    if isinstance(value, str) and len(value) == 2 and value.isascii() and value.isalpha():
        return value.upper()
    return None


class CountryRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["allow", "exclude"] = "exclude"
    countries: tuple[Country, ...] = ()

    def accepts(self, known: Iterable[str]) -> bool:
        return not self.countries or all(
            (code in self.countries) == (self.mode == "allow") for code in known
        )


class CountryFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    maker: CountryRule = Field(default_factory=CountryRule)
    serving: CountryRule = Field(default_factory=CountryRule)
    keep_unknown: bool = False

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
        maker = MAKERS.get(slug.partition("/")[0])
        if maker and not self.filters.maker.accepts([maker[0]]):
            return f"Maker country {maker[0]} is excluded.", []
        if not maker and not self.filters.keep_unknown:
            return "Maker country is unknown.", []
        if model.startswith(("local:", "openai:", "anthropic:")):
            if self.filters.keep_unknown:
                return None, []
            return "Provider country is unknown.", []
        allowed = []
        for provider in self.models.get(slug, []):
            hq, centers = self.providers.get(provider, (None, []))
            if (not hq or not centers or None in centers) and not self.filters.keep_unknown:
                continue
            if self.filters.serving.accepts(code for code in [hq, *centers] if code):
                allowed.append(provider)
        if not allowed:
            return (
                "No allowed provider serves this model. Change the filters or pick another model.",
                [],
            )
        return None, allowed

    def describe(self, model: str) -> dict:
        slug = model.removeprefix("openrouter:")
        maker = MAKERS.get(slug.partition("/")[0])
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
    def __init__(self, path: Path) -> None:
        self.path = path
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
        return CountryCatalog(self.filters(), providers, models)

    async def refresh(
        self, model_ids: Iterable[str], client: httpx.AsyncClient, base_url: str
    ) -> None:
        ids = set(model_ids)
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
