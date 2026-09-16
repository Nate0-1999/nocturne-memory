"""Public API contract version and its mechanical OpenAPI drift guard."""

from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256
from typing import Any

API_CONTRACT_VERSION = "0.1.20"


class ApiContractDriftError(RuntimeError):
    """Raised when client-facing OpenAPI changes under an existing version."""


def openapi_contract_fingerprint(document: dict[str, Any]) -> str:
    """Hash client-facing OpenAPI while excluding the product release version."""

    contract = deepcopy(document)
    info = contract.get("info")
    if isinstance(info, dict):
        info.pop("version", None)
    encoded = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def require_known_contract_fingerprint(
    document: dict[str, Any],
    fingerprints: dict[str, str],
    *,
    version: str = API_CONTRACT_VERSION,
) -> str:
    """Return the fingerprint or reject unversioned client-contract drift."""

    actual = openapi_contract_fingerprint(document)
    expected = fingerprints.get(version)
    if expected is None:
        raise ApiContractDriftError(
            f"api_contract_version {version!r} has no recorded OpenAPI fingerprint"
        )
    if actual != expected:
        raise ApiContractDriftError(
            "client-facing OpenAPI changed under api_contract_version "
            f"{version!r}; bump API_CONTRACT_VERSION before recording the new fingerprint"
        )
    return actual
