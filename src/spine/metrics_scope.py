"""M3SC read scope under the existing shared-token, declared-principal contract."""

from typing import Literal

from fastapi import HTTPException, Request


def metrics_principal(
    request: Request, principal_id: str, scope: Literal["principal", "palace"]
) -> str | None:
    if scope == "palace":
        # F094: verification identities must not read the owner's whole-Palace metrics.
        if principal_id != request.app.state.settings.owner_principal_id:
            raise HTTPException(403, "Only the Palace owner can view the whole Palace.")
        return None
    return principal_id
