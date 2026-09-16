"""M2A acceptance for receipt ingestion, append-only law, and views."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spine.spend.contracts import SpendTableSnapshot
from spine.spend.views import CANONICAL_SPEND_VIEWS, SpendViewRefresher


async def test_manual_invoice_is_owner_only_replay_safe_and_counted_on_its_day(
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
    memory_app,
) -> None:
    """ADR-024 / M3SR: one owner-entered bill, no duplicate charge or broker cost."""
    memory_app.state.settings.owner_principal_id = "invoice-owner"
    invoice = {
        "invoice_id": "GCP-test-2026-09",
        "invoice_date": "2026-09-01",
        "amount_usd": "25.15",
    }
    url = "/v1/spend/invoices?principal_id=invoice-owner"
    denied = await memory_client.post("/v1/spend/invoices?principal_id=visitor", json=invoice)
    assert denied.status_code == 403
    replies = await asyncio.gather(*(memory_client.post(url, json=invoice) for _ in range(2)))
    assert [response.status_code for response in replies] == [200, 200]
    for changed in ({"amount_usd": "26"}, {"invoice_date": "2026-09-02"}):
        assert (await memory_client.post(url, json={**invoice, **changed})).status_code == 409
    assert (await memory_client.post(url, json={**invoice, "amount_usd": "-1"})).status_code == 422
    async with memory_session_factory() as session:
        rows = (
            await session.execute(text("SELECT product_type, cost_usd, ts FROM spend_event"))
        ).all()
    assert len(rows) == 1
    assert rows[0].product_type == "infra.run.serve"
    assert rows[0].cost_usd == Decimal("25.15")
    assert rows[0].ts.date().isoformat() == "2026-09-01"
    table = (
        await memory_client.get("/v1/spend/table?principal_id=invoice-owner&scope=palace")
    ).json()
    assert table["can_record_invoice"] is True
    day = table["days"][0]
    assert Decimal(day["infrastructure_usd"]) == Decimal("25.15")
    assert Decimal(day["total_usd"]) == Decimal("25.15")
    assert day["model_usd"] is None  # No model receipt is fabricated by entering a bill.
    visitor = (await memory_client.get("/v1/spend/table?principal_id=visitor")).json()
    assert visitor["can_record_invoice"] is False
    assert visitor["days"] == []


_EVENT_UID = "01K1M2A0000000000000000001"
_SECOND_UID = "01K1M2A0000000000000000002"
_THREAD_ID = "11111111-1111-4111-8111-111111111111"
_MEMORY_ID = "22222222-2222-4222-8222-222222222222"


def _event(
    event_uid: str = _EVENT_UID,
    *,
    quantity_type: str = "input_fresh",
    quantity: str = "125",
    cost_usd: str | None = "0.000250",
    ts: str = "2026-08-01T12:34:56.789Z",
    purpose: str = "building",
    thread_id: str | None = _THREAD_ID,
    model: str | None = "anthropic/claude-sonnet-4.6",
) -> dict[str, object]:
    return {
        "event_uid": event_uid,
        "ts": ts,
        "product_type": "llm.request",
        "quantity_type": quantity_type,
        "unit_of_measure": "tokens",
        "quantity": quantity,
        "cost_usd": cost_usd,
        "basis": "measured",
        "behavior": "variable",
        "purpose": purpose,
        "principal_id": "owner",
        "machine_id": "workstation",
        "origin_agent": "harness-chat",
        "thread_id": thread_id,
        "run_id": "01K1M2A0000000000000000003",
        "prompt_id": "01K1M2A0000000000000000004",
        "memory_id": _MEMORY_ID,
        "model": model,
        "provider": "anthropic",
        "quantization": None,
        "ref": "gen-test-1",
        "meta": {"cost_source": "provider_details"},
    }


async def test_spend_route_is_atomic_idempotent_and_conflict_safe(
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-027 is defended by verifying that spend route is atomic idempotent and conflict safe;
    this prevents drift in the append-only spend-ledger contract.
    """
    first = await memory_client.post("/v1/spend/events", json={"events": [_event()]})
    replay = await memory_client.post("/v1/spend/events", json={"events": [_event()]})

    assert first.status_code == 200
    assert first.json() == {"accepted": 1}
    assert replay.status_code == 200
    assert replay.json() == {"accepted": 1}

    conflict = _event(quantity="126")
    new_line = _event(_SECOND_UID, quantity_type="output", quantity="40")
    rejected = await memory_client.post(
        "/v1/spend/events",
        json={"events": [new_line, conflict]},
    )

    assert rejected.status_code == 409
    assert rejected.headers["content-type"].startswith("application/problem+json")
    assert _EVENT_UID in rejected.json()["detail"]
    async with memory_session_factory() as session:
        count = await session.scalar(text("SELECT count(*) FROM spend_event"))
    assert count == 1


async def test_spend_contract_rejects_zero_lines_bad_enums_and_duplicate_ids(
    memory_client: AsyncClient,
) -> None:
    """A-027 is defended by verifying that spend contract rejects zero lines bad enums and
    duplicate ids; this prevents drift in the append-only spend-ledger contract.
    """
    zero = _event(quantity="0")
    duplicate = _event()
    bad_purpose = _event()
    bad_purpose["purpose"] = "misc"

    for body in (
        {"events": []},
        {"events": [zero]},
        {"events": [duplicate, duplicate]},
        {"events": [bad_purpose]},
    ):
        response = await memory_client.post("/v1/spend/events", json=body)
        assert response.status_code == 422


async def test_spend_table_groups_threads_models_token_lanes_and_non_thread_purposes(
    memory_client: AsyncClient,
) -> None:
    """ADR-024 reads one exact global projection and one repeated-thread ATTUNED slice."""

    now = datetime.now(UTC)
    current = now.isoformat()
    old = (now - timedelta(hours=2)).isoformat()
    second_thread = "33333333-3333-4333-8333-333333333333"
    second_model = "openai/gpt-5.4-mini"

    def uid(suffix: str) -> str:
        return f"{_EVENT_UID[:-1]}{suffix}"

    events = [
        _event(uid("3"), quantity="100", cost_usd="0.010", ts=current),
        _event(uid("4"), quantity_type="input_cached", quantity="50", cost_usd="0.002", ts=current),
        _event(uid("5"), quantity_type="cache_write", quantity="25", cost_usd="0.001", ts=current),
        _event(uid("6"), quantity_type="reasoning", quantity="10", cost_usd="0.005", ts=current),
        _event(uid("7"), quantity_type="output", quantity="20", cost_usd="0.004", ts=current),
        _event(uid("8"), quantity="30", cost_usd="0.003", ts=old, model=second_model),
        _event(uid("9"), quantity="12", cost_usd="0.002", ts=current, thread_id=second_thread),
        _event(
            uid("A"),
            quantity="5",
            cost_usd="0.0000007",
            ts=current,
            purpose="embedding",
            thread_id=None,
            model="text-embedding-3-small",
        ),
        _event(
            uid("B"),
            quantity="7",
            cost_usd=None,
            ts=current,
            purpose="curation",
            thread_id=None,
            model="minimax/minimax-m3",
        ),
    ]
    written = await memory_client.post("/v1/spend/events", json={"events": events})
    assert written.status_code == 200

    response = await memory_client.get("/v1/spend/table?principal_id=local&scope=palace")
    assert response.status_code == 200
    snapshot = SpendTableSnapshot.model_validate(response.json())
    by_thread = {str(row.thread_id): row for row in snapshot.threads}
    first = by_thread[_THREAD_ID]
    assert first.input_tokens == Decimal("130")
    assert first.kv_cache_tokens == Decimal("75")
    assert first.reasoning_tokens == Decimal("10")
    assert first.output_tokens == Decimal("20")
    assert first.total_usd == Decimal("0.025")
    assert first.spend_per_hour_usd == Decimal("0.022")
    assert [row.model for row in first.models] == [
        "anthropic/claude-sonnet-4.6",
        second_model,
    ]
    assert [(row.purpose, row.label) for row in snapshot.purposes] == [
        ("curation", "Memory keeping"),
        ("embedding", "Embeddings"),
    ]
    assert snapshot.purposes[0].total_usd is None
    assert snapshot.purposes[0].total_unpriced_lines == 1
    assert response.json()["purposes"][1]["total_usd"] == "0.000000700000"
    assert response.json()["purposes"][1]["spend_per_hour_usd"] == "0.000000700000"

    scoped_response = await memory_client.get(
        "/v1/spend/table",
        params=[("thread_id", _THREAD_ID), ("principal_id", "local"), ("scope", "palace")],
    )
    scoped = SpendTableSnapshot.model_validate(scoped_response.json())
    assert [str(row.thread_id) for row in scoped.threads] == [_THREAD_ID]
    assert scoped.purposes == []
    assert scoped.rate_source == "spend_event"
    total = next(lane for lane in scoped.rates if lane.dimension == "total")
    assert sum(Decimal(point.cost_usd) for point in total.points) == Decimal("0.022")
    assert len(scoped.messages) == 1
    assert Decimal(scoped.messages[0].fresh_tokens) == Decimal("130")
    assert Decimal(scoped.messages[0].cached_tokens) == Decimal("50")
    assert Decimal(scoped.messages[0].cache_write_tokens) == Decimal("25")

    empty_response = await memory_client.get("/v1/spend/table?principal_id=owner&scope=threads")
    empty = SpendTableSnapshot.model_validate(empty_response.json())
    assert empty.threads == []
    assert empty.purposes == []
    assert empty.rates == empty.messages == empty.days == []


@pytest.mark.parametrize("origin", ["run/root.1", "harness-agent/01M2M9GH426AH8972483N6CD0D"])
async def test_spend_history_filters_principal_and_keeps_unknown_prices(
    memory_client: AsyncClient,
    origin: str,
) -> None:
    """ADR-024 / M3SC / M3SR: charts and cache history cannot leak another principal."""
    now = datetime.now(UTC).isoformat()
    own = _event(ts=now)
    own.update(principal_id="verification", origin_agent=origin)
    other = _event(_SECOND_UID, ts=now, model="private-model")
    other.update(principal_id="other", cost_usd="100")
    unknown = _event("01K1M2A0000000000000000005", ts=now, cost_usd=None)
    unknown.update(principal_id="verification", purpose="curation", thread_id=None)
    result = await memory_client.post("/v1/spend/events", json={"events": [own, other, unknown]})
    assert result.status_code == 200
    result = await memory_client.get("/v1/spend/table?principal_id=verification")
    assert result.status_code == 200
    assert "private-model" not in result.text
    snapshot = SpendTableSnapshot.model_validate(result.json())
    assert any(lane.dimension == "subagent" for lane in snapshot.rates)
    curation = next(lane for lane in snapshot.rates if lane.dimension == "curation")
    assert curation.points[0].cost_usd is None
    assert snapshot.days[0].unpriced_lines == 1
    assert Decimal(snapshot.days[0].total_usd) == Decimal("0.00025")
    assert snapshot.messages[0].thread_id == UUID(_THREAD_ID)


async def test_spend_event_database_is_append_only(
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-027 is defended by verifying that spend event database is append only; this prevents
    drift in the append-only spend-ledger contract.
    """
    response = await memory_client.post("/v1/spend/events", json={"events": [_event()]})
    assert response.status_code == 200

    for statement in (
        "UPDATE spend_event SET quantity = 1 WHERE event_uid = :event_uid",
        "DELETE FROM spend_event WHERE event_uid = :event_uid",
    ):
        async with memory_session_factory() as session:
            with pytest.raises(DBAPIError, match="spend_event is append-only"):
                async with session.begin():
                    await session.execute(text(statement), {"event_uid": _EVENT_UID})


async def test_canonical_views_are_double_run_deterministic_and_sentence_readable(
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-027 is defended by verifying that canonical views are double run deterministic and
    sentence readable; this prevents drift in the append-only spend-ledger contract.
    """
    cached = _event(
        _SECOND_UID,
        quantity_type="input_cached",
        quantity="375",
        cost_usd="0.000075",
    )
    response = await memory_client.post(
        "/v1/spend/events",
        json={"events": [_event(), cached]},
    )
    assert response.status_code == 200

    refresher = SpendViewRefresher(memory_session_factory)
    snapshots: list[bytes] = []
    for _ in range(2):
        await refresher.refresh_once()
        snapshots.append(await _view_snapshot(memory_session_factory))

    assert snapshots[0] == snapshots[1]
    decoded = json.loads(snapshots[0])
    assert set(decoded) == set(CANONICAL_SPEND_VIEWS)
    efficiency = decoded["v_cache_efficiency"][0]
    assert Decimal(efficiency["cache_efficiency"]) == Decimal("0.75")

    async with memory_session_factory() as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT product_type, quantity, unit_of_measure, quantity_type, "
                        "cost_usd, basis, purpose, model, provider, ref "
                        "FROM spend_event WHERE event_uid = :event_uid"
                    ),
                    {"event_uid": _EVENT_UID},
                )
            )
            .mappings()
            .one()
        )
    sentence = (
        f"{row['purpose']} bought {row['quantity']} {row['unit_of_measure']} of "
        f"{row['quantity_type']} for {row['product_type']} on {row['model']} from "
        f"{row['provider']}, costing ${row['cost_usd']} ({row['basis']}), ref {row['ref']}."
    )
    assert sentence == (
        "building bought 125.000000000 tokens of input_fresh for llm.request on "
        "anthropic/claude-sonnet-4.6 from anthropic, costing $0.000250000000 "
        "(measured), ref gen-test-1."
    )


async def test_receipt_language_is_shipped_as_database_comments(
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-027 is defended by verifying that receipt language is shipped as database comments;
    this prevents drift in the append-only spend-ledger contract.
    """
    async with memory_session_factory() as session:
        table_comment = await session.scalar(
            text("SELECT obj_description('spend_event'::regclass, 'pg_class')")
        )
        comments = dict(
            (
                await session.execute(
                    text(
                        "SELECT a.attname, col_description(a.attrelid, a.attnum) "
                        "FROM pg_attribute a "
                        "WHERE a.attrelid = 'spend_event'::regclass AND a.attnum > 0"
                    )
                )
            ).all()
        )

    assert table_comment is not None and "receipt line" in table_comment
    assert comments["basis"] is not None and "Honesty column" in comments["basis"]
    assert comments["ref"] is not None and "price class" in comments["ref"]


async def _view_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
) -> bytes:
    result: dict[str, object] = {}
    ordering = {
        "v_spend_rate": "minute, purpose, model, provider",
        "v_thread_cost": "thread_id",
        "v_run_cost": "run_id, thread_id",
        "v_memory_cost": "memory_id",
        "v_cache_efficiency": "minute, model, provider",
    }
    async with session_factory() as session:
        for view in CANONICAL_SPEND_VIEWS:
            rows = (
                (await session.execute(text(f"SELECT * FROM {view} ORDER BY {ordering[view]}")))
                .mappings()
                .all()
            )
            result[view] = [_json_row(dict(row)) for row in rows]
    return json.dumps(result, sort_keys=True, separators=(",", ":")).encode()


def _json_row(row: dict[str, object]) -> dict[str, object]:
    return {
        key: (
            value.isoformat()
            if isinstance(value, datetime)
            else str(value)
            if isinstance(value, (Decimal, UUID))
            else value
        )
        for key, value in row.items()
    }
