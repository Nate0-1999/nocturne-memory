"""Shared A-020/A-021 model policy selection for Harness roles and Palace curators."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal, DecimalException, InvalidOperation, localcontext
from typing import Literal, Protocol

import httpx

_PRICE_PER_MILLION = Decimal(1_000_000)
_CATALOG_TTL_SECONDS = 24 * 60 * 60
_LOG_PRECISION = 48

logger = logging.getLogger(__name__)


class ModelPolicyConfigurationError(ValueError):
    """An operator supplied a model policy outside A-021's exact grammar."""


class ModelCatalogUnavailable(RuntimeError):
    """The broker catalog cannot produce an auditable policy decision."""


class NamedModelResolutionError(ValueError):
    """A human `/model` target is not a valid broker-listed OpenRouter route."""


@dataclass(frozen=True, slots=True)
class ModelPolicy:
    """One parsed A-021 policy."""

    kind: Literal["pinned", "max", "elbow", "slope", "floor"]
    value: str | Decimal | None = None


@dataclass(frozen=True, slots=True)
class BenchmarkModel:
    """One complete Artificial Analysis row, with prices normalized to USD/M tokens."""

    permaslug: str
    intelligence_index: Decimal
    prompt_price: Decimal
    completion_price: Decimal


@dataclass(frozen=True, slots=True)
class ModelRoute:
    """One unambiguous OpenRouter request ID and its matching context window."""

    model_id: str
    context_tokens: int
    input_modalities: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class ListedModel:
    """FL-202: one source model as the model browser shows it; prices in USD/M tokens."""

    model_id: str
    name: str
    context_tokens: int | None
    prompt_price: Decimal | None
    completion_price: Decimal | None
    reasoning: bool
    intelligence_index: Decimal | None = None
    # SD-079: the request parameters the source says this model takes; None when unpublished.
    supported_parameters: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class ModelCatalog:
    """A fetched benchmark table plus executable routes from the same snapshot."""

    rows: tuple[BenchmarkModel, ...]
    model_routes: Mapping[str, ModelRoute]
    fetched_at: datetime
    listing: tuple[ListedModel, ...] = ()


@dataclass(frozen=True, slots=True)
class ThreadModelResolution:
    """The daemon-lifetime model truth shared by display, memory, and model calls."""

    model: str
    context_tokens: int
    policy: str
    price_sorted: bool = False
    input_modalities: frozenset[str] | None = None
    benchmark: BenchmarkModel | None = None
    catalog_fetched_at: datetime | None = None
    stickiness_epoch: int = 0
    request_parameters: ModelRequestParameters = field(
        default_factory=lambda: ModelRequestParameters()
    )

    def __post_init__(self) -> None:
        _validate_model_name(self.model)
        if type(self.context_tokens) is not int or self.context_tokens <= 0:
            raise ValueError("context_tokens must be a positive integer")
        if type(self.stickiness_epoch) is not int or self.stickiness_epoch < 0:
            raise ValueError("stickiness_epoch must be a non-negative integer")
        if self.input_modalities is not None and (
            not isinstance(self.input_modalities, frozenset)
            or any(
                not isinstance(value, str) or not value or value != value.strip()
                for value in self.input_modalities
            )
        ):
            raise ValueError("input_modalities must be a frozenset of nonblank strings or None")

    @property
    def uses_openrouter(self) -> bool:
        return self.model.startswith("openrouter:")


@dataclass(frozen=True, slots=True)
class ModelRequestParameters:
    """Nullable per-thread broker overrides; null inherits provider behavior."""

    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    max_tokens: int | None = None
    effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None = None

    def __post_init__(self) -> None:
        if self.temperature is not None and not 0 <= self.temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if self.top_p is not None and not 0 <= self.top_p <= 1:
            raise ValueError("top_p must be between 0 and 1")
        if self.top_k is not None and (type(self.top_k) is not int or not 0 <= self.top_k <= 500):
            raise ValueError("top_k must be an integer between 0 and 500")
        if self.max_tokens is not None and (
            type(self.max_tokens) is not int or not 1 <= self.max_tokens <= 131_072
        ):
            raise ValueError("max_tokens must be an integer between 1 and 131072")


class ModelCatalogLoader(Protocol):
    """Fetch the current broker table, using a cache no older than 24 hours."""

    async def load(self) -> ModelCatalog: ...

    async def load_named_route(self, model_id: str) -> tuple[ModelRoute, datetime]: ...


class ThreadModelResolver(Protocol):
    """Return the stable model decision for one daemon-lifetime thread."""

    async def resolve(self, thread_id: str) -> ThreadModelResolution: ...

    async def resolve_named(self, thread_id: str, model: str) -> ThreadModelResolution: ...

    async def resolve_hydrated(
        self,
        thread_id: str,
        model: str,
    ) -> ThreadModelResolution: ...

    async def resolve_image_capability(
        self,
        thread_id: str,
        resolution: ThreadModelResolution,
    ) -> ThreadModelResolution: ...


def parse_model_policy(value: str) -> ModelPolicy:
    """Parse exactly the five lowercase A-021 policy forms."""

    if not isinstance(value, str) or not value or value != value.strip():
        raise ModelPolicyConfigurationError(
            "model policy must be nonblank without surrounding whitespace"
        )
    if value == "max":
        return ModelPolicy("max")
    if value == "elbow":
        return ModelPolicy("elbow")
    if value.startswith("pinned:"):
        model = value.removeprefix("pinned:")
        _validate_model_name(model)
        return ModelPolicy("pinned", model)
    for prefix, kind in (("slope:", "slope"), ("floor:", "floor")):
        if value.startswith(prefix):
            raw_number = value.removeprefix(prefix)
            try:
                number = Decimal(raw_number)
            except InvalidOperation as exc:
                raise ModelPolicyConfigurationError(
                    f"{kind} policy requires a positive finite number"
                ) from exc
            if not number.is_finite() or number <= 0:
                raise ModelPolicyConfigurationError(
                    f"{kind} policy requires a positive finite number"
                )
            return ModelPolicy(kind, number)  # type: ignore[arg-type]
    raise ModelPolicyConfigurationError(
        "model policy must be pinned:<model>, max, elbow, slope:<lambda>, or floor:<n>"
    )


def select_model(policy: ModelPolicy, rows: Sequence[BenchmarkModel]) -> BenchmarkModel:
    """Select one benchmark row under the exact A-021 deterministic rules."""

    if not rows:
        raise ModelCatalogUnavailable("benchmark table has no complete rows")
    if policy.kind == "max":
        return _select_max(rows)
    if policy.kind == "floor":
        assert isinstance(policy.value, Decimal)
        eligible = [row for row in rows if row.intelligence_index >= policy.value]
        if not eligible:
            raise ModelCatalogUnavailable("benchmark table has no model meeting the floor")
        return min(
            eligible,
            key=lambda row: (row.prompt_price, row.completion_price, row.permaslug),
        )

    try:
        frontier = pareto_frontier(rows)
        if policy.kind == "elbow":
            return _select_elbow(frontier, elbow_price_floor(frontier, rows))
        if policy.kind == "slope":
            assert isinstance(policy.value, Decimal)
            return _select_slope(frontier, policy.value)
    except DecimalException as exc:
        raise ModelCatalogUnavailable("benchmark table arithmetic is degenerate") from exc
    raise ModelPolicyConfigurationError("pinned policies do not consult the benchmark table")


def pareto_frontier(rows: Sequence[BenchmarkModel]) -> tuple[BenchmarkModel, ...]:
    """Return the cost-intelligence frontier with A-021 coordinate deduplication."""

    by_coordinate: dict[tuple[Decimal, Decimal], BenchmarkModel] = {}
    for row in rows:
        coordinate = (row.intelligence_index, row.prompt_price)
        incumbent = by_coordinate.get(coordinate)
        if incumbent is None or row.permaslug < incumbent.permaslug:
            by_coordinate[coordinate] = row
    unique = tuple(by_coordinate.values())
    frontier = [
        row
        for row in unique
        if not any(
            other is not row
            and other.intelligence_index >= row.intelligence_index
            and other.prompt_price <= row.prompt_price
            and (
                other.intelligence_index > row.intelligence_index
                or other.prompt_price < row.prompt_price
            )
            for other in unique
        )
    ]
    return tuple(sorted(frontier, key=lambda row: row.intelligence_index))


def elbow_price_floor(
    frontier: Sequence[BenchmarkModel],
    rows: Sequence[BenchmarkModel],
) -> Decimal | None:
    """SPEC C.5 (v2.129): the price elbow gives a free frontier model, the table's lowest nonzero
    prompt price; None when elbow prices no free model."""

    if len(frontier) < 3 or frontier[0].prompt_price > 0:
        return None
    return min(row.prompt_price for row in rows if row.prompt_price > 0)


def lower_convex_hull(
    frontier: Sequence[BenchmarkModel],
) -> tuple[BenchmarkModel, ...]:
    """Stretch A-021's lower taut string while retaining collinear vertices."""

    hull: list[BenchmarkModel] = []
    for row in frontier:
        while len(hull) >= 2 and _cross(hull[-2], hull[-1], row) < 0:
            hull.pop()
        hull.append(row)
    return tuple(hull)


class OpenRouterCatalogClient:
    """Fetch and cache the two OpenRouter tables used by non-pinned policies."""

    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str = "https://openrouter.ai/api/v1",
        http_client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._api_key = api_key.strip() if api_key is not None else None
        self._base_url = base_url.rstrip("/")
        self._client = http_client
        self._owns_client = http_client is None
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic or time.monotonic
        self._cached: ModelCatalog | None = None
        self._cached_at_monotonic: float | None = None
        self._lock = asyncio.Lock()

    async def load(self) -> ModelCatalog:
        cached = self._fresh_cache()
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._fresh_cache()
            if cached is not None:
                return cached
            client = self._request_client()
            try:
                benchmark_response, models_response = await asyncio.gather(
                    client.get(
                        f"{self._base_url}/benchmarks",
                        params={"source": "artificial-analysis"},
                    ),
                    client.get(f"{self._base_url}/models"),
                )
                benchmark_payload = _response_json(benchmark_response, "benchmarks")
                models_payload = _response_json(models_response, "models")
                fetched_at = self._clock()
                if fetched_at.tzinfo is None:
                    raise ModelCatalogUnavailable("catalog clock returned a naive timestamp")
                rows = _parse_benchmark_rows(benchmark_payload)
                catalog = ModelCatalog(
                    rows=rows,
                    model_routes=_parse_model_routes(models_payload),
                    fetched_at=fetched_at,
                    listing=parse_model_listing(models_payload, rows),
                )
            except ModelCatalogUnavailable:
                raise
            except (httpx.HTTPError, ValueError, TypeError, DecimalException) as exc:
                raise ModelCatalogUnavailable("OpenRouter catalog request failed") from exc
            self._cached = catalog
            self._cached_at_monotonic = self._monotonic()
            return catalog

    async def load_named_route(self, model_id: str) -> tuple[ModelRoute, datetime]:
        """Fetch one exact model-list route without consulting benchmark cache."""

        async with self._lock:
            client = self._request_client()
            try:
                response = await client.get(f"{self._base_url}/models")
                payload = _response_json(response, "models")
                fetched_at = self._clock()
                if fetched_at.tzinfo is None:
                    raise ModelCatalogUnavailable("catalog clock returned a naive timestamp")
                route = _parse_named_routes(payload).get(model_id)
                if route is None:
                    raise NamedModelResolutionError(f"unknown OpenRouter model: {model_id}")
                return route, fetched_at
            except (ModelCatalogUnavailable, NamedModelResolutionError):
                raise
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                raise ModelCatalogUnavailable("OpenRouter model-list request failed") from exc

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _fresh_cache(self) -> ModelCatalog | None:
        if self._cached is None or self._cached_at_monotonic is None:
            return None
        age = self._monotonic() - self._cached_at_monotonic
        if 0 <= age < _CATALOG_TTL_SECONDS:
            return self._cached
        return None

    def _request_client(self) -> httpx.AsyncClient:
        if not self._api_key:
            raise ModelCatalogUnavailable(
                "OPENROUTER_API_KEY is unavailable for model policy lookup"
            )
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0),
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        return self._client


class ModelPolicyResolver:
    """Resolve and retain one auditable model choice per daemon-lifetime thread."""

    def __init__(
        self,
        *,
        policy: str,
        static_model: str,
        static_context_tokens: int,
        catalog: ModelCatalogLoader | None,
    ) -> None:
        self._policy_text = policy
        self._policy = parse_model_policy(policy)
        _validate_model_name(static_model)
        if type(static_context_tokens) is not int or static_context_tokens <= 0:
            raise ModelPolicyConfigurationError(
                "static model context tokens must be a positive integer"
            )
        self._static_model = static_model
        self._static_context_tokens = static_context_tokens
        self._catalog = catalog
        self._resolutions: dict[str, ThreadModelResolution] = {}
        self._image_resolutions: dict[tuple[str, str, int], ThreadModelResolution] = {}
        # M4AH: image input the catalog confirmed for a model, kept for the daemon's life.
        self._image_input: dict[str, frozenset[str]] = {}
        self._lock = asyncio.Lock()

    def set_policy(self, policy: str) -> None:
        """A-021 / FL-154: new threads adopt role policy; existing choices remain sticky."""
        parsed = parse_model_policy(policy)
        self._policy_text, self._policy = policy, parsed

    def explain(self, thread_id: str, model: str, *, explicitly_selected: bool = False) -> str:
        """FL-152/153: explain the retained choice, never today's replacement policy."""
        if explicitly_selected:
            return "Explicit model choice for this conversation."
        resolution = self._resolutions.get(thread_id)
        if resolution is None or resolution.model != model:
            return "Retained conversation model; its original policy evidence is unavailable."
        policy = parse_model_policy(resolution.policy)
        if policy.kind == "pinned":
            return "Pinned model selected for this conversation."
        benchmark = resolution.benchmark
        if benchmark is None:
            return (
                f"{policy.kind.title()} could not resolve the catalog; "
                "using the configured fallback."
            )
        reason = {
            "max": "Highest intelligence score in the catalog",
            "floor": f"Cheapest model meeting intelligence floor {policy.value}",
            "elbow": "Elbow of the intelligence versus log-price frontier",
            "slope": (
                f"Frontier choice allowing ${policy.value}/M input tokens per intelligence point"
            ),
        }[policy.kind]
        return (
            f"{reason}. Score {benchmark.intelligence_index}; "
            f"input ${benchmark.prompt_price}/M, output ${benchmark.completion_price}/M tokens. "
            "This token-cost policy does not impose an hourly dollar limit."
        )

    async def resolve(self, thread_id: str) -> ThreadModelResolution:
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("thread_id must not be blank")
        resolved = self._resolutions.get(thread_id)
        if resolved is not None:
            return resolved
        async with self._lock:
            resolved = self._resolutions.get(thread_id)
            if resolved is not None:
                return resolved
            resolved = await self._resolve_uncached(thread_id)
            self._resolutions[thread_id] = resolved
            return resolved

    async def resolve_named(self, thread_id: str, model: str) -> ThreadModelResolution:
        """Resolve one explicit adapter model string without mutating thread truth."""

        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("thread_id must not be blank")
        slug = _parse_named_model(self._catalog, model)
        if self._catalog is None:
            raise ModelCatalogUnavailable("model router catalog is not configured")

        route, fetched_at = await self._catalog.load_named_route(slug)

        resolved = ThreadModelResolution(
            model=_qualify_model(self._catalog, route.model_id),
            context_tokens=route.context_tokens,
            policy="human_command",
            input_modalities=route.input_modalities,
            catalog_fetched_at=fetched_at,
        )
        logger.info(
            "named model resolved thread=%s requested=%s model=%s context_tokens=%s",
            thread_id,
            model,
            resolved.model,
            resolved.context_tokens,
        )
        return resolved

    async def resolve_hydrated(
        self,
        thread_id: str,
        model: str,
    ) -> ThreadModelResolution:
        """Restore sticky model identity while catalog truth is optional. [A-052]"""

        try:
            return await self.resolve_named(thread_id, model)
        except (ModelCatalogUnavailable, NamedModelResolutionError) as exc:
            logger.warning(
                "hydrated model catalog unavailable; retaining identity "
                "thread=%s model=%s reason=%s",
                thread_id,
                model,
                str(exc),
            )
            return ThreadModelResolution(
                model=model,
                context_tokens=self._static_context_tokens,
                policy="hydrated_unverified",
            )

    async def resolve_image_capability(
        self,
        thread_id: str,
        resolution: ThreadModelResolution,
    ) -> ThreadModelResolution:
        """Resolve exact OpenRouter image-input truth only for an image turn. [A-052]"""

        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("thread_id must not be blank")
        if not resolution.uses_openrouter or resolution.input_modalities is not None:
            return resolution
        if self._catalog is None:
            raise ModelCatalogUnavailable("OpenRouter catalog is not configured")

        cache_key = (thread_id, resolution.model, resolution.stickiness_epoch)
        cached = self._image_resolutions.get(cache_key)
        if cached is not None:
            return cached
        if (known := self._image_input.get(resolution.model)) is not None:
            # M4AH walk: a thread first resolved while the catalog was unreachable refused an image
            # the catalog had already confirmed for this model; its earlier word stands.
            return replace(resolution, input_modalities=known)

        slug = resolution.model.removeprefix("openrouter:")
        route, fetched_at = await self._catalog.load_named_route(slug)
        if route.model_id != slug:
            raise NamedModelResolutionError(f"unknown OpenRouter model: {slug}")
        enriched = replace(
            resolution,
            input_modalities=route.input_modalities,
            catalog_fetched_at=fetched_at,
        )
        # Only positive, structurally valid catalog truth is retained for the
        # stickiness epoch. Unknown capability may be retried on a later turn.
        if enriched.input_modalities is not None:
            self._image_input[resolution.model] = enriched.input_modalities
            async with self._lock:
                current = self._image_resolutions.get(cache_key)
                if current is None:
                    self._image_resolutions[cache_key] = enriched
                    current = enriched
                retained = self._resolutions.get(thread_id)
                if (
                    retained is not None
                    and retained.model == resolution.model
                    and retained.stickiness_epoch == resolution.stickiness_epoch
                ):
                    self._resolutions[thread_id] = current
                return current
        return enriched

    async def _resolve_uncached(self, thread_id: str) -> ThreadModelResolution:
        if self._policy.kind == "pinned":
            assert isinstance(self._policy.value, str)
            context_tokens = self._static_context_tokens
            input_modalities = None
            if self._policy.value != self._static_model and self._catalog is not None:
                # A pick other than the configured model takes its own window (M3SK); the
                # benchmark table stays unconsulted, and an unknown route keeps the static one.
                try:
                    named = await self.resolve_named(thread_id, self._policy.value)
                    context_tokens = named.context_tokens
                    # M3W6A: the route's image input was dropped here, so an image turn fetched
                    # the catalog again and one failed fetch refused a model that takes images.
                    input_modalities = named.input_modalities
                    if input_modalities is not None:
                        self._image_input[self._policy.value] = input_modalities
                except (ModelCatalogUnavailable, NamedModelResolutionError):
                    pass
            resolved = ThreadModelResolution(
                model=self._policy.value,
                context_tokens=context_tokens,
                policy=self._policy_text,
                input_modalities=input_modalities,
            )
            logger.info(
                "model policy resolved thread=%s policy=%s model=%s",
                thread_id,
                self._policy_text,
                resolved.model,
            )
            return resolved

        try:
            if self._catalog is None:
                raise ModelCatalogUnavailable("OpenRouter catalog is not configured")
            catalog = await self._catalog.load()
            selected = select_model(self._policy, catalog.rows)
            route = catalog.model_routes.get(selected.permaslug)
            if route is None:
                raise ModelCatalogUnavailable(
                    "selected model has no unambiguous positive-context route"
                )
        except ModelCatalogUnavailable as exc:
            logger.warning(
                "model policy failed open thread=%s policy=%s model=%s reason=%s",
                thread_id,
                self._policy_text,
                self._static_model,
                str(exc),
            )
            return ThreadModelResolution(
                model=self._static_model,
                context_tokens=self._static_context_tokens,
                policy=self._policy_text,
                price_sorted=True,
            )

        resolved = ThreadModelResolution(
            model=_qualify_model(self._catalog, route.model_id),
            context_tokens=route.context_tokens,
            policy=self._policy_text,
            price_sorted=True,
            input_modalities=route.input_modalities,
            benchmark=selected,
            catalog_fetched_at=catalog.fetched_at,
        )
        logger.info(
            "model policy resolved thread=%s policy=%s model=%s "
            "model_route=%s "
            "intelligence_index=%s prompt_usd_per_m=%s completion_usd_per_m=%s "
            "catalog_fetched_at=%s",
            thread_id,
            self._policy_text,
            selected.permaslug,
            route.model_id,
            selected.intelligence_index,
            selected.prompt_price,
            selected.completion_price,
            catalog.fetched_at.isoformat(),
        )
        return resolved


def _select_max(rows: Sequence[BenchmarkModel]) -> BenchmarkModel:
    return min(
        rows,
        key=lambda row: (-row.intelligence_index, row.prompt_price, row.permaslug),
    )


def _parse_named_model(catalog: ModelCatalogLoader | None, model: str) -> str:
    parser = getattr(catalog, "parse_named_model", None)
    if callable(parser):
        parsed = parser(model)
        if not isinstance(parsed, str) or not parsed:
            raise NamedModelResolutionError("model router returned an invalid model id")
        return parsed
    if (
        not isinstance(model, str)
        or not model.startswith("openrouter:")
        or model != model.strip()
        or not model.removeprefix("openrouter:")
    ):
        raise NamedModelResolutionError("model must be an openrouter:<broker-model-id> string")
    return model.removeprefix("openrouter:")


def _qualify_model(catalog: ModelCatalogLoader, model_id: str) -> str:
    qualifier = getattr(catalog, "qualify_model", None)
    if callable(qualifier):
        qualified = qualifier(model_id)
        _validate_model_name(qualified)
        return qualified
    return f"openrouter:{model_id}"


def _select_elbow(frontier: Sequence[BenchmarkModel], floor: Decimal | None) -> BenchmarkModel:
    if not frontier:
        raise ModelCatalogUnavailable("benchmark frontier is empty")
    if len(frontier) < 3:
        return _select_max(frontier)

    min_index = frontier[0].intelligence_index
    max_index = frontier[-1].intelligence_index
    with localcontext() as context:
        context.prec = _LOG_PRECISION
        log_prices = [
            (row.prompt_price if floor is None or row.prompt_price else floor).log10()
            for row in frontier
        ]
        min_log_price = log_prices[0]
        max_log_price = log_prices[-1]
        index_span = max_index - min_index
        log_price_span = max_log_price - min_log_price
        if index_span <= 0 or log_price_span <= 0:
            raise ModelCatalogUnavailable("elbow frontier has a degenerate axis")
        positive: list[tuple[Decimal, BenchmarkModel]] = []
        for row, log_price in zip(frontier, log_prices, strict=True):
            x = (row.intelligence_index - min_index) / index_span
            y = (log_price - min_log_price) / log_price_span
            offset = x - y
            if offset > 0:
                positive.append((offset, row))
    if not positive:
        return _select_max(frontier)
    return min(positive, key=lambda item: (-item[0], item[1].prompt_price, item[1].permaslug))[1]


def _select_slope(
    frontier: Sequence[BenchmarkModel],
    willingness_to_pay: Decimal,
) -> BenchmarkModel:
    if len(frontier) < 2:
        raise ModelCatalogUnavailable("slope frontier has fewer than two points")
    hull = lower_convex_hull(frontier)
    selected = hull[0]
    for lower, upper in zip(hull, hull[1:], strict=False):
        price_delta = upper.prompt_price - lower.prompt_price
        index_delta = upper.intelligence_index - lower.intelligence_index
        if price_delta <= willingness_to_pay * index_delta:
            selected = upper
        else:
            break
    return selected


def _cross(
    first: BenchmarkModel,
    middle: BenchmarkModel,
    last: BenchmarkModel,
) -> Decimal:
    return (middle.intelligence_index - first.intelligence_index) * (
        last.prompt_price - first.prompt_price
    ) - (middle.prompt_price - first.prompt_price) * (
        last.intelligence_index - first.intelligence_index
    )


def _response_json(response: httpx.Response, label: str) -> object:
    if not response.is_success:
        raise ModelCatalogUnavailable(
            f"OpenRouter {label} request returned HTTP {response.status_code}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise ModelCatalogUnavailable(f"OpenRouter {label} response was not JSON") from exc


def _parse_benchmark_rows(payload: object) -> tuple[BenchmarkModel, ...]:
    data = _payload_data(payload, "benchmark")
    rows: dict[str, BenchmarkModel] = {}
    for raw in data:
        if not isinstance(raw, dict) or raw.get("source") != "artificial-analysis":
            continue
        pricing = raw.get("pricing")
        if not isinstance(pricing, dict):
            continue
        permaslug = raw.get("model_permaslug")
        intelligence = _finite_decimal(raw.get("intelligence_index"))
        prompt = _finite_decimal(pricing.get("prompt"))
        completion = _finite_decimal(pricing.get("completion"))
        if (
            not isinstance(permaslug, str)
            or not permaslug
            or permaslug != permaslug.strip()
            or intelligence is None
            or prompt is None
            or completion is None
            or prompt < 0
            or completion < 0
        ):
            continue
        row = BenchmarkModel(
            permaslug=permaslug,
            intelligence_index=intelligence,
            prompt_price=prompt * _PRICE_PER_MILLION,
            completion_price=completion * _PRICE_PER_MILLION,
        )
        existing = rows.get(permaslug)
        if existing is not None and existing != row:
            raise ModelCatalogUnavailable("benchmark table contains conflicting model rows")
        rows[permaslug] = row
    if not rows:
        raise ModelCatalogUnavailable("benchmark table has no complete rows")
    return tuple(rows.values())


def _parse_model_routes(payload: object) -> Mapping[str, ModelRoute]:
    data = _payload_data(payload, "models")
    grouped: dict[str, dict[str, tuple[object, frozenset[str] | None]]] = {}
    canonical_by_id: dict[str, str] = {}
    for raw in data:
        if not isinstance(raw, dict):
            continue
        model_id = raw.get("id")
        permaslug = raw.get("canonical_slug")
        context_length = raw.get("context_length")
        input_modalities = _parse_input_modalities(raw)
        if (
            not isinstance(model_id, str)
            or not model_id
            or model_id != model_id.strip()
            or not isinstance(permaslug, str)
            or not permaslug
            or permaslug != permaslug.strip()
        ):
            continue
        previous_canonical = canonical_by_id.get(model_id)
        if previous_canonical is not None and previous_canonical != permaslug:
            raise ModelCatalogUnavailable("model table maps one route to multiple models")
        canonical_by_id[model_id] = permaslug
        routes = grouped.setdefault(permaslug, {})
        route_value = (context_length, input_modalities)
        if model_id in routes and routes[model_id] != route_value:
            raise ModelCatalogUnavailable("model table contains conflicting route rows")
        routes[model_id] = route_value

    resolved: dict[str, ModelRoute] = {}
    for permaslug, by_id in grouped.items():
        standard_ids = tuple(model_id for model_id in by_id if ":" not in model_id)
        selected_id: str | None = None
        if len(standard_ids) == 1:
            selected_id = standard_ids[0]
        elif not standard_ids and len(by_id) == 1:
            selected_id = next(iter(by_id))
        if selected_id is None:
            continue
        context_length, input_modalities = by_id[selected_id]
        if type(context_length) is int and context_length > 0:
            resolved[permaslug] = ModelRoute(
                model_id=selected_id,
                context_tokens=context_length,
                input_modalities=input_modalities,
            )
    if not resolved:
        raise ModelCatalogUnavailable("model table has no unambiguous positive-context routes")
    return resolved


def _parse_named_routes(payload: object) -> Mapping[str, ModelRoute]:
    """Retain every exact positive-context broker ID for explicit `/model`."""

    data = _payload_data(payload, "models")
    resolved: dict[str, ModelRoute] = {}
    for raw in data:
        if not isinstance(raw, dict):
            continue
        model_id = raw.get("id")
        context_length = raw.get("context_length")
        input_modalities = _parse_input_modalities(raw)
        if (
            not isinstance(model_id, str)
            or not model_id
            or model_id != model_id.strip()
            or type(context_length) is not int
            or context_length <= 0
        ):
            continue
        previous = resolved.get(model_id)
        route = ModelRoute(
            model_id=model_id,
            context_tokens=context_length,
            input_modalities=input_modalities,
        )
        if previous is not None and previous != route:
            raise ModelCatalogUnavailable("model table contains conflicting named routes")
        resolved[model_id] = route
    if not resolved:
        raise ModelCatalogUnavailable("model table has no positive-context named routes")
    return resolved


def parse_model_listing(
    payload: object,
    rows: Sequence[BenchmarkModel] = (),
) -> tuple[ListedModel, ...]:
    """FL-202: every priced source model, joined to the benchmark index the policies use.

    A-020 rejects per-prompt classifier routing, so router pseudo-models (variable
    price, or OpenRouter's own `openrouter/` routers) are never offered.
    """

    index = {row.permaslug: row.intelligence_index for row in rows}
    listed: dict[str, ListedModel] = {}
    for raw in _payload_data(payload, "models"):
        if not isinstance(raw, dict):
            continue
        model_id = raw.get("id")
        if not isinstance(model_id, str) or not model_id or model_id != model_id.strip():
            continue
        pricing = raw.get("pricing")
        prompt = completion = None
        if isinstance(pricing, dict):
            prompt = _finite_decimal(pricing.get("prompt"))
            completion = _finite_decimal(pricing.get("completion"))
        if model_id.startswith("openrouter/") or any(
            price is not None and price < 0 for price in (prompt, completion)
        ):
            continue
        name = raw.get("name")
        context_length = raw.get("context_length")
        supported = raw.get("supported_parameters")
        listed[model_id] = ListedModel(
            model_id=model_id,
            name=name.strip() if isinstance(name, str) and name.strip() else model_id,
            context_tokens=(
                context_length if type(context_length) is int and context_length > 0 else None
            ),
            prompt_price=None if prompt is None else prompt * _PRICE_PER_MILLION,
            completion_price=None if completion is None else completion * _PRICE_PER_MILLION,
            reasoning=isinstance(supported, list) and "reasoning" in supported,
            intelligence_index=index.get(str(raw.get("canonical_slug"))),
            supported_parameters=(
                frozenset(item for item in supported if isinstance(item, str))
                if isinstance(supported, list)
                else None
            ),
        )
    return tuple(listed.values())


def _parse_input_modalities(raw: Mapping[str, object]) -> frozenset[str] | None:
    architecture = raw.get("architecture")
    if not isinstance(architecture, dict):
        return None
    value = architecture.get("input_modalities")
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item or item != item.strip() for item in value
    ):
        return None
    return frozenset(value)


def _payload_data(payload: object, label: str) -> list[object]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ModelCatalogUnavailable(f"OpenRouter {label} response has no data array")
    return payload["data"]


def _finite_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _validate_model_name(model: str) -> None:
    if not isinstance(model, str) or not model or model != model.strip():
        raise ModelPolicyConfigurationError(
            "pinned model must be nonblank without surrounding whitespace"
        )
    if ":" not in model:
        raise ModelPolicyConfigurationError(
            "pinned model must use the existing provider:model syntax"
        )
    provider, model_name = model.split(":", 1)
    if (
        not provider
        or not model_name
        or not all(part.strip() == part for part in (provider, model_name))
    ):
        raise ModelPolicyConfigurationError(
            "pinned model must use the existing provider:model syntax"
        )
