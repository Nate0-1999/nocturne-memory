"""Unbuilt rules remain visible as skips, never fictional passing checks (PLAN M3RC)."""

import pytest


@pytest.mark.skip(reason="r11-2: shared Palaces and revocable principal grants are horizon work")
def test_r11_2_shared_palace_grants_are_explicit_lineaged_and_revocable() -> None:
    """ADR-020 requires explicit sharing and read access revoked on the next request."""
    pytest.fail("Replace this placeholder with revocation acceptance proof when built")


@pytest.mark.skip(reason="r9-9: worker/local cost stores have not converged into the spend ledger")
def test_r9_9_spend_events_are_the_only_cost_store_and_receive_worker_receipts() -> None:
    """ADR-024 requires one cost system with worker receipts through /v1/spend/events."""
    pytest.fail("Replace this placeholder with one-ledger worker acceptance proof when built")


@pytest.mark.skip(reason="r9-6: strongest curator model policy awaits SD-018")
def test_r9_6_curators_resolve_the_strongest_model_policy() -> None:
    """SPEC C.5 / P4.2 requires the compounding curator role to use the strongest policy."""
    pytest.fail("Replace this placeholder with curator-policy acceptance proof when built")
