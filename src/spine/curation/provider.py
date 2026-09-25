"""LLM verdict seam for curator findings; judgment only, never writes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol

import httpx

from spine.curation.contracts import (
    CuratorVerdictDraft,
    HealthFinding,
    PalaceHealthReport,
)
from spine.ids import mint_ulid
from spine.spend.contracts import SpendEventInput
from spine.spend.service import SpendService


class CuratorVerdictProvider(Protocol):
    async def verdict(
        self,
        finding: HealthFinding,
        report: PalaceHealthReport,
        *,
        run_uid: str,
        machine_id: str,
    ) -> CuratorVerdictDraft: ...


class CuratorProviderError(RuntimeError):
    """The verdict model failed or returned an invalid bounded verdict."""


class UnavailableCuratorProvider:
    """Fail plainly when a Palace has no configured curator model credential."""

    async def verdict(
        self,
        finding: HealthFinding,
        report: PalaceHealthReport,
        *,
        run_uid: str,
        machine_id: str,
    ) -> CuratorVerdictDraft:
        del finding, report, run_uid, machine_id
        raise CuratorProviderError("curator model is not configured")


class OpenRouterCuratorProvider:
    """One structured OpenRouter call per deterministic finding."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        spend_service: SpendService,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout: float = 45.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("curator provider requires an OpenRouter key")
        self._api_key = api_key
        self._provider = "openrouter" if base_url == "https://openrouter.ai/api/v1" else "openai"
        self._model = _openrouter_model(model) if self._provider == "openrouter" else model
        self._spend_service = spend_service
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._timeout = timeout
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None

    async def verdict(
        self,
        finding: HealthFinding,
        report: PalaceHealthReport,
        *,
        run_uid: str,
        machine_id: str,
    ) -> CuratorVerdictDraft:
        prompt = _verdict_prompt(finding, report)
        try:
            response = await self._client.post(
                self._endpoint,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "temperature": 0,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a minimally invasive memory curator. Read the supplied "
                                "deterministic finding. Return one JSON object matching the "
                                "schema. Judge only; never claim you changed data. Prefer keep "
                                "when uncertain."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {"type": "json_object"},
                },
                timeout=self._timeout,
            )
        except httpx.RequestError as exc:
            raise CuratorProviderError("curator verdict request failed") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise CuratorProviderError(
                f"curator verdict provider returned HTTP {response.status_code}"
            )
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("content is not text")
            draft = CuratorVerdictDraft.model_validate_json(_strip_fence(content))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise CuratorProviderError("curator verdict response was malformed") from exc
        await self._receipt(
            payload,
            response=response,
            run_uid=run_uid,
            machine_id=machine_id,
            principal_id=report.principal_id,
            finding=finding,
        )
        return draft

    async def _receipt(
        self,
        payload: object,
        *,
        response: httpx.Response,
        run_uid: str,
        machine_id: str,
        principal_id: str,
        finding: HealthFinding,
    ) -> None:
        data = payload if isinstance(payload, dict) else {}
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        raw_cost = usage.get("cost")
        try:
            cost = None if raw_cost is None else Decimal(str(raw_cost))
        except InvalidOperation:
            cost = None
        event = SpendEventInput(
            event_uid=mint_ulid(),
            ts=datetime.now(UTC),
            product_type="llm.request",
            quantity_type="request",
            unit_of_measure="request",
            quantity=Decimal(1),
            cost_usd=cost,
            basis="measured" if cost is not None else "estimated",
            behavior="variable",
            purpose="curation",
            principal_id=principal_id,
            machine_id=machine_id,
            origin_agent="maintenance",
            run_id=run_uid,
            model=str(data.get("model") or self._model),
            provider=self._provider,
            ref=response.headers.get("x-request-id") or f"curator:{run_uid}:{finding.ordinal}",
            meta={
                "finding_kind": finding.kind,
                "prompt_tokens": _json_int(usage.get("prompt_tokens")),
                "completion_tokens": _json_int(usage.get("completion_tokens")),
            },
        )
        await self._spend_service.append([event])

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _verdict_prompt(finding: HealthFinding, report: PalaceHealthReport) -> str:
    allowed = {
        "duplicate": ["keep", "merge"],
        "contradiction": ["keep", "contradict", "supersede"],
        "stale": ["keep", "supersede", "retire"],
        "slop": ["keep", "retire", "split"],
        "keyword": ["keep", "keyword_repair"],
    }[finding.kind]
    return json.dumps(
        {
            "health_report": {
                "format": report.format,
                "version": report.version,
                "corpus_revision": report.corpus_revision,
                "active_units": report.active_units,
            },
            "finding": finding.model_dump(mode="json"),
            "allowed_actions": allowed,
            "schema": CuratorVerdictDraft.model_json_schema(),
            "surgeon_order": ["keep", "edge", "edit", "merge", "rewrite"],
            "constraints": [
                "preserve every qualifier",
                "split only on semantic boundaries",
                "use 2-5 distinct lowercase keywords",
                "when uncertain choose keep",
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _openrouter_model(model: str) -> str:
    provider, separator, name = model.partition(":")
    if separator and provider == "openrouter":
        return name
    return f"{provider}/{name}" if separator else model


def _strip_fence(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        first_newline = stripped.find("\n")
        if first_newline >= 0:
            stripped = stripped[first_newline + 1 : -3].strip()
    return stripped


def _json_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


__all__ = [
    "CuratorProviderError",
    "CuratorVerdictProvider",
    "OpenRouterCuratorProvider",
    "UnavailableCuratorProvider",
]
